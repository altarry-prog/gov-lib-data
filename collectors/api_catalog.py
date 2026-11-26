"""공공데이터포털 전체 목록에서 문화정책에 쓸 오픈API 고르기(api-catalog).

원천: 공공데이터활용지원센터_공공데이터포털 목록개방현황(포털 15062804, 매월, 이용허락범위 제한 없음).
이 파일데이터의 자동 변환 API(api.odcloud.kr)로 전체 목록을 읽고, 오픈API만 남겨 점수를 매긴 뒤
기관당 개수를 묶어 폭넓게 N개(기본 200)를 고른다.

결과
- catalog/api_catalog.csv : 고른 N개(점수·분야·기관·이용허락범위·활용신청 건수·목록 URL). 전화번호 칸은 저장하지 않는다
- catalog/api_catalog.meta.json : 원천·수집일·이용조건·전체/오픈API/고른 수
- --to-candidates : 고른 것을 catalog/candidates.json 에 더한다(spec-fetch 가 명세를 받게)

사용: DATA_GO_KR_KEY=... python3 collectors/api_catalog.py run [--top 200] [--per-org 8] [--to-candidates]
"""
import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import spec_fetch  # noqa: E402  화면에 박힌 명세 읽기를 같이 쓴다

PK = "15062804"
SRC = f"https://www.data.go.kr/data/{PK}/fileData.do"
OUT_CSV = "catalog/api_catalog.csv"
OUT_META = "catalog/api_catalog.meta.json"
LICENSE = "공공데이터포털 목록개방현황(이용허락범위 제한 없음). 각 API 이용조건은 '이용허락범위' 칸"

# 분야: (이름, 낱말들). 목록명·키워드·설명·분류체계에서 찾는다.
AXES = [
    ("도서관·독서·출판", "도서관 독서 도서 책 출판 서지 사서 작은도서관 서점 문해"),
    ("문화예술", "문화 예술 공연 전시 박물관 미술관 문예 음악 미술 연극 무용 국악 공예 예술인"),
    ("생활문화·축제·관광", "축제 관광 여가 생활문화 동호회 문화시설 관광지 행사 체험"),
    ("국가유산·기록", "문화재 국가유산 유물 기록물 아카이브 고문서 역사 향토"),
    ("콘텐츠·미디어", "콘텐츠 영화 방송 게임 웹툰 저작권 음반 미디어 언론 뉴스"),
    ("체육", "체육 스포츠 생활체육 운동"),
    ("교육·평생학습", "교육 학교 평생학습 강좌 학습 늘봄 돌봄 진로 대학"),
    ("대상 인구", "인구 주민등록 아동 청소년 청년 노인 고령 다문화 외국인 장애 1인가구 가구 출생 영유아 여성 가족"),
    ("복지·포용", "복지 취약 저소득 바우처 문화누리 기초생활 돌봄 접근성"),
    ("재정·공모·행정", "예산 재정 보조금 공모 지원사업 계약 입찰 조례 법령 입법 의회 회의록 감사 민원 정책"),
    ("지역·공간", "행정구역 행정동 상권 유동인구 생활인구 도시 지역 마을 공간 시설"),
    ("디지털·AI", "디지털 정보화 AI 인공지능 데이터 정보격차 메타데이터 LOD"),
]
CATS = {"문화관광": 3, "교육": 2, "사회복지": 2, "일반공공행정": 1, "지역개발": 1, "과학기술": 1, "보건": 1, "재정금융": 1}
DROP = re.compile(r"기상|날씨|미세먼지|대기|수질|하천|댐|선박|항만|어업|농산물 가격|축산|병해충|지하수|교통사고|버스 노선|지하철|항공|특허|의약품|식품첨가|부동산 실거래|아파트 매매|주차장 실시간")


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers=spec_fetch.UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def find_path(raw):
    """파일데이터 화면에 박힌 swagger 에서 odcloud 경로(/15062804/v1/uddi:...)를 찾는다. 가장 최근 것(마지막)."""
    specs, _ = spec_fetch.embedded_specs(raw)
    paths = [p for s in specs for p in (s.get("paths") or {}) if PK in p]
    if not paths:
        paths = re.findall(r"/" + PK + r"/v1/uddi:[0-9a-f-]+", raw)
    return sorted(set(paths))[-1] if paths else None


def fetch_rows(key, get=http_get, per=1000, delay=0.3, log=print):
    raw = get(SRC).decode("utf-8", "replace")
    path = find_path(raw)
    if not path:
        raise RuntimeError("목록개방현황 API 경로를 화면에서 못 찾음")
    rows, page, total = [], 1, None
    while True:
        q = urllib.parse.urlencode({"page": page, "perPage": per, "returnType": "JSON", "serviceKey": key})
        data = json.loads(get(f"https://api.odcloud.kr/api{path}?{q}").decode("utf-8", "replace"))
        total = data.get("totalCount", total)
        got = data.get("data") or []
        rows += got
        if not got or (total and len(rows) >= total) or page > 400:
            break
        page += 1
        time.sleep(delay)
    log(f"목록 {len(rows)}/{total}건 ({path})")
    return rows, total, path


def g(r, *names):
    for n in names:
        if r.get(n) not in (None, ""):
            return str(r[n])
    return ""


def score(r):
    text = " ".join(g(r, k) for k in ("목록명", "키워드", "설명", "분류체계"))
    if DROP.search(text):
        return 0, []
    axes = [name for name, words in AXES if any(w in text for w in words.split())]
    cat = g(r, "분류체계").split("-")[0].strip()
    base = 3 * len(axes) + CATS.get(cat, 0)
    if not axes:
        return 0, []
    try:
        used = float(g(r, "다운로드_활용신청건수", "다운로드(활용신청)건수") or 0)
        views = float(g(r, "조회수") or 0)
    except ValueError:
        used = views = 0
    pop = math.log10(1 + used) * 2 + math.log10(1 + views)
    free = 1 if "제한 없음" in g(r, "이용허락범위") else 0
    return round(base + pop + free, 2), axes


def pick(rows, top=200, per_org=8):
    api = [r for r in rows if "오픈API" in g(r, "목록유형") or "API" == g(r, "목록유형")]
    scored = []
    for r in api:
        s, axes = score(r)
        if s > 0:
            scored.append((s, axes, r))
    scored.sort(key=lambda x: -x[0])
    out, per, seen_axis = [], {}, {}
    # 1차: 분야마다 최소 8개 보장(폭), 2차: 점수 순. 기관당 per_org 개까지
    for want_axis in [name for name, _ in AXES]:
        for s, axes, r in scored:
            if len(out) >= top or seen_axis.get(want_axis, 0) >= 8:
                break
            org, key = g(r, "제공기관"), g(r, "목록키")
            if want_axis in axes and per.get(org, 0) < per_org and key not in {g(x[2], "목록키") for x in out}:
                out.append((s, axes, r)); per[org] = per.get(org, 0) + 1
                for a in axes:
                    seen_axis[a] = seen_axis.get(a, 0) + 1
    for s, axes, r in scored:
        if len(out) >= top:
            break
        org, key = g(r, "제공기관"), g(r, "목록키")
        if per.get(org, 0) < per_org and key not in {g(x[2], "목록키") for x in out}:
            out.append((s, axes, r)); per[org] = per.get(org, 0) + 1
    out.sort(key=lambda x: -x[0])
    return api, scored, out


COLS = ["순위", "점수", "분야", "pk", "목록명", "제공기관", "분류체계", "API 유형", "데이터포맷", "이용허락범위", "업데이트 주기",
        "활용신청 건수", "조회수", "수정일", "키워드", "목록 URL"]


def to_row(i, s, axes, r):
    key = g(r, "목록키")
    url = g(r, "목록 URL", "목록URL") or (f"https://www.data.go.kr/data/{key}/openapi.do" if key.isdigit() else "")
    return {"순위": i, "점수": s, "분야": "·".join(axes), "pk": key, "목록명": g(r, "목록명"), "제공기관": g(r, "제공기관"),
            "분류체계": g(r, "분류체계"), "API 유형": g(r, "API 유형"), "데이터포맷": g(r, "확장자(데이터포맷)", "확장자"),
            "이용허락범위": g(r, "이용허락범위"), "업데이트 주기": g(r, "업데이트 주기"),
            "활용신청 건수": g(r, "다운로드_활용신청건수"), "조회수": g(r, "조회수"), "수정일": g(r, "수정일"),
            "키워드": spec_fetch.mask(g(r, "키워드"))[:200], "목록 URL": url}


def run(root=".", key=None, get=http_get, top=200, per_org=8, to_candidates=False, today=None, log=print):
    today = today or dt.date.today().isoformat()
    rows, total, path = fetch_rows(key, get=get, log=log)
    api, scored, chosen = pick(rows, top=top, per_org=per_org)
    out = [to_row(i + 1, s, axes, r) for i, (s, axes, r) in enumerate(chosen)]
    with open(os.path.join(root, OUT_CSV), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(out)
    axes_count = {name: sum(name in x["분야"].split("·") for x in out) for name, _ in AXES}
    meta = {"자료": "문화정책용 공공데이터포털 오픈API 목록", "원천": SRC, "원천 API 경로": path, "수집일": today, "이용조건": LICENSE,
            "전체 목록": total, "받은 목록": len(rows), "오픈API": len(api), "관련 있음": len(scored), "고른 수": len(out),
            "분야별": axes_count, "기관 수": len({x["제공기관"] for x in out}),
            "고르는 법": "목록명·키워드·설명·분류체계에서 분야 낱말 수 ×3 + 분류 가산 + 활용신청·조회 로그 + 제한 없음 1점. 분야마다 최소 8개, 기관당 최대 %d개" % per_org}
    with open(os.path.join(root, OUT_META), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    if to_candidates:
        p = os.path.join(root, spec_fetch.CANDIDATES)
        with open(p, encoding="utf-8") as f:
            c = json.load(f)
        have = {x["pk"] for x in c["candidates"]}
        for x in out:
            if x["pk"].isdigit() and x["pk"] not in have:
                c["candidates"].append({"pk": x["pk"], "이름": x["목록명"], "쓰는 곳": "api-catalog " + x["분야"]})
                have.add(x["pk"])
        with open(p, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    log(json.dumps({k: meta[k] for k in ("전체 목록", "오픈API", "관련 있음", "고른 수", "기관 수")}, ensure_ascii=False))
    return out, meta


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--top", type=int, default=200)
    r.add_argument("--per-org", type=int, default=8)
    r.add_argument("--to-candidates", action="store_true")
    a = ap.parse_args(argv)
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        print("DATA_GO_KR_KEY 가 없습니다")
        return 1
    try:
        run(key=key, top=a.top, per_org=a.per_org, to_candidates=a.to_candidates)
    except Exception as e:  # noqa: BLE001
        print("실패:", spec_fetch.mask(str(e)).replace(key, "***")[:300])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
