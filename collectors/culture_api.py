"""문화·도서관 공개 API 묶음 수집기. 시군구 단위 비교에 쓰도록 목록(공공시설)과 집계(업소)를 남긴다.

원칙(CLAUDE.md):
- 인증키는 환경변수로만. 응답·로그·파일에 키를 남기지 않는다(오류 문구도 가린다).
- 전화번호·좌표·개인 이름·이메일 칸은 버린다. 인허가 업소는 목록을 남기지 않고 시군구별 개수만 남긴다.
- 결과마다 meta.json(원천 주소·수집일·건수·필드 이름·이용조건)을 붙인다.
- 처음 보는 응답 구조는 data/_probe/<id>.json 에 필드 이름만 남겨 다음 수정에 쓴다.

사용: python3 collectors/culture_api.py run [--only id1,id2] [--max-pages N]
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = "data"
TODAY = dt.date.today()
DROP = re.compile(r"(tel|phone|fax|전화|팩스|연락처|contact|mail|이메일|lat|lon|lng|위도|경도|mapx|mapy|좌표|crd|_x$|_y$|^x$|^y$|"
                  r"rprsv|대표자|ceo|owner|담당자|관리자명)", re.I)
ADDR_KEYS = ("ROAD_NM_ADDR", "LOTNO_ADDR", "rdnmadr", "lnmadr", "addr1", "address", "ADRES", "adres", "addr",
             "fcob_nm", "faci_road_addr", "faci_addr", "소재지도로명주소", "소재지지번주소", "주소", "도로명주소", "지번주소")
SIDO_KEYS = ("ctprvnNm", "sido", "sidonm", "ctprvn_nm", "CTPRV_NM", "CTPRVN_NM", "areaNm", "시도명", "시도")
SGG_KEYS = ("signguNm", "sggNm", "gungu", "gugunnm", "signgu_nm", "SGG_NM", "SIGNGU_NM", "CODE_VALUE", "sigunguNm", "시군구명", "시군구")
LICENSE = "각 원천 기관 이용조건(공공데이터포털 이용허락범위·공공누리)을 따른다"

DG = "https://apis.data.go.kr"
JOBS = [
    # 전국문화기반시설총람(한국문화정보원, 15125097): 공공시설 목록
    *[{"id": f"chongram-{k}", "name": f"문화기반시설총람 {n}", "kind": "facility", "key": "DATA_GO_KR_KEY",
       "url": f"{DG}/B553457/rgnCltrFcltExmnv1/{op}", "params": {"resultType": "json", "instNm": ""},
       "year_param": "pblshYr", "page": ("pageNo", "numOfRows", 1000), "src": "https://www.data.go.kr/data/15125097/openapi.do"}
      for k, n, op in [("lib", "공공도서관", "clifLbrryv1"), ("natlib", "국립도서관", "clifNtnLbrryv1"),
                       ("museum", "박물관", "clifMsmv1"), ("gallery", "미술관", "clifArglv1"),
                       ("lvcl", "생활문화센터", "clifLvclCntrv1"), ("arts-hall", "문예회관", "clifClcnv1"),
                       ("culture-center", "지방문화원", "clifLclcv1"), ("culture-house", "문화의집", "clifClhsv1"),
                       ("foundation", "지역문화재단", "clifLcclFndtv1"), ("literature", "문학관", "clifLtrm1")]],
    # 행정안전부 문화 인허가(2025-11 신규, 매일 갱신): 업소는 개수만
    *[{"id": f"license-{k}", "name": f"인허가 {n}", "kind": "business", "key": "DATA_GO_KR_KEY",
       "url": [f"{DG}/1741000/{s}/info" for s in slugs], "params": {"returnType": "json"},
       "page": ("pageNo", "numOfRows", 1000), "src": f"https://www.data.go.kr/data/{pk}/openapi.do"}
      for k, n, slugs, pk in [("film-screening", "영화상영업", ["film_screenings"], 15154857),
                              ("movie-theater", "영화상영관", ["movie_theaters"], 15154848),
                              ("museum-gallery", "박물관·미술관", ["museums_and_art_galleries"], 15155146),
                              ("local-culture-center", "지방문화원", ["local_culture_centers"], 15155246),
                              ("karaoke", "노래연습장업", ["karaoke_rooms"], 15155135),
                              ("arts-corp", "문화예술법인", ["cultural_art_corporations"], 15155142),
                              ("performance-hall", "공연장", ["performance_halls", "performance_venues", "theaters"], 15154966),
                              ("tour-performance", "관광공연장업", ["tourist_performance_halls", "tourism_performance_halls",
                                                               "tourist_performance_venues"], 15154970)]],
    {"id": "nl-libraries", "name": "국립중앙도서관 도서관 정보", "kind": "facility", "key": "DATA_GO_KR_KEY",
     "url": f"{DG}/1371029/LibraryInformationService/getLibraryList", "params": {"type": "json"},
     "page": ("pageNo", "numOfRows", 1000), "src": "https://www.data.go.kr/data/15154373/openapi.do"},
    {"id": "sports-facilities", "name": "공공체육시설(국민체육진흥공단)", "kind": "facility", "key": "DATA_GO_KR_KEY",
     "url": f"{DG}/B551014/SRVC_SFMS_FACIL_INFO/TODZ_SFMS_FACIL_INFO", "params": {"resultType": "json"},
     "page": ("pageNo", "numOfRows", 1000), "src": "https://www.data.go.kr/data/15107764/openapi.do"},
    {"id": "arte-groups", "name": "문화예술교육 자원지도 단체", "kind": "facility", "key": "DATA_GO_KR_KEY",
     "url": f"{DG}/B551463/arteApiService/getGroupInfo", "params": {"resultType": "json"},
     "page": ("pageNo", "numOfRows", 1000), "src": "https://www.data.go.kr/data/15140200/openapi.do"},
    {"id": "reading-rooms", "name": "공공도서관 열람실(전국 통합)", "kind": "facility", "key": "DATA_GO_KR_KEY",
     "url": f"{DG}/B551982/plr_v2/info_v2", "params": {"type": "json"},
     "page": ("pageNo", "numOfRows", 1000), "src": "https://www.data.go.kr/data/15142580/openapi.do"},
    {"id": "tour-culture-venues", "name": "관광정보 문화시설(TourAPI)", "kind": "facility", "key": "DATA_GO_KR_KEY",
     "url": f"{DG}/B551011/KorService2/areaBasedList2",
     "params": {"_type": "json", "MobileOS": "ETC", "MobileApp": "govlibdata", "contentTypeId": "14", "arrange": "A"},
     "page": ("pageNo", "numOfRows", 1000), "src": "https://www.data.go.kr/data/15101578/openapi.do"},
    {"id": "tour-festivals", "name": "관광정보 축제공연행사(TourAPI)", "kind": "facility", "key": "DATA_GO_KR_KEY",
     "url": f"{DG}/B551011/KorService2/areaBasedList2",
     "params": {"_type": "json", "MobileOS": "ETC", "MobileApp": "govlibdata", "contentTypeId": "15", "arrange": "A"},
     "page": ("pageNo", "numOfRows", 1000), "src": "https://www.data.go.kr/data/15101578/openapi.do"},
    {"id": "tour-visitors", "name": "기초지자체 방문자 수(최근 30일 합)", "kind": "series", "key": "DATA_GO_KR_KEY",
     "url": f"{DG}/B551011/DataLabService/locgoRegnVisitrDDList",
     "params": {"_type": "json", "MobileOS": "ETC", "MobileApp": "govlibdata",
                "startYmd": (TODAY - dt.timedelta(days=33)).strftime("%Y%m%d"),
                "endYmd": (TODAY - dt.timedelta(days=4)).strftime("%Y%m%d")},
     "page": ("pageNo", "numOfRows", 1000), "src": "https://www.data.go.kr/data/15101972/openapi.do"},
    {"id": "seoul-libraries", "name": "서울시 공공도서관", "kind": "facility", "key": "SEOUL_KEY",
     "url": "http://openapi.seoul.go.kr:8088/{key}/json/SeoulPublicLibraryInfo/{start}/{end}/", "seoul": True,
     "src": "https://data.seoul.go.kr"},
    {"id": "kopis-venues", "name": "공연시설(KOPIS)", "kind": "facility", "key": "KOPIS_KEY", "xml": True,
     "url": "https://www.kopis.or.kr/openApi/restful/prfplc", "keyparam": "service", "params": {},
     "page": ("cpage", "rows", 1000), "src": "https://www.kopis.or.kr/por/cs/openapi/openApiInfo.do"},
    # 문화공공데이터광장: 호출 주소를 원천 안내 화면에서 찾는다
    *[{"id": k, "name": n, "kind": "facility", "key": env, "kcisa_page": pid,
       "params": {}, "page": ("pageNo", "numOfRows", 1000), "src": f"https://www.culture.go.kr/data/openapi/openapiView.do?id={pid}"}
      for k, n, env, pid in [("indie-bookstores", "전국 독립서점", "KCISA_BOOKSTORE_KEY", 623),
                             ("used-bookstores", "전국 중고서점", "KCISA_USEDBOOK_KEY", 547)]],
]


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "gov-lib-data", "Accept": "application/json, */*"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def redact(text, key):
    if not key:
        return text
    return text.replace(key, "***").replace(urllib.parse.quote(key, safe=""), "***")


def xml_items(text):
    root = ET.fromstring(text)
    out = []
    for el in root.iter():
        kids = list(el)
        if kids and all(len(list(k)) == 0 for k in kids) and len(kids) >= 2:
            out.append({k.tag: (k.text or "").strip() for k in kids})
    return out


def find_items(obj):
    """응답 안에서 가장 큰 '사전들의 목록'을 찾는다(기관마다 구조가 달라서)."""
    best = []

    def walk(o):
        nonlocal best
        if isinstance(o, list):
            if o and all(isinstance(x, dict) for x in o) and len(o) > len(best):
                best = o
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            vals = list(o.values())
            if o and all(isinstance(v, (str, int, float, type(None))) for v in vals) and not best:
                pass
            for v in vals:
                walk(v)
    walk(obj)
    if not best and isinstance(obj, dict):
        # item 이 하나뿐이라 사전으로 오는 경우
        for k in ("item", "row", "items"):
            def dig(o):
                if isinstance(o, dict):
                    if k in o and isinstance(o[k], dict):
                        return [o[k]]
                    for v in o.values():
                        r = dig(v)
                        if r:
                            return r
                return None
            r = dig(obj)
            if r:
                return r
    return best


def total_count(obj):
    m = re.search(r'"(?:totalCount|total_count|list_total_count|totalCnt)"\s*:\s*"?(\d+)', json.dumps(obj, ensure_ascii=False))
    return int(m.group(1)) if m else None


SIDO_SHORT = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시", "광주": "광주광역시",
              "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도", "강원": "강원특별자치도",
              "충북": "충청북도", "충남": "충청남도", "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도",
              "경남": "경상남도", "제주": "제주특별자치도"}


def region_of(row):
    sido = next((str(row[k]).strip() for k in SIDO_KEYS if row.get(k)), "")
    sgg = next((str(row[k]).strip() for k in SGG_KEYS if row.get(k)), "")
    if not (sido and sgg):
        addr = next((str(row[k]).strip() for k in ADDR_KEYS if row.get(k)), "")
        toks = addr.replace("(", " ").split()
        if toks:
            sido = sido or toks[0]
            if not sgg and len(toks) > 1:
                sgg = toks[1]
                # 시 아래 구가 있으면 시 이름으로 묶는다(예: 부천시 원미구 → 부천시)
    for short, full in SIDO_SHORT.items():
        if sido.startswith(short):
            sido = full if len(sido) <= len(short) + 1 else sido
            break
    return sido, sgg


def clean_row(row):
    return {k: v for k, v in row.items() if not DROP.search(str(k)) and not isinstance(v, (dict, list))}


def fetch_kcisa_url(page_id, get=http_get):
    html = get(f"https://www.culture.go.kr/data/openapi/openapiView.do?id={page_id}")
    m = re.search(r"https://api\.kcisa\.kr/openapi/[A-Za-z0-9_/\-.]+", html)
    return m.group(0) if m else None


def build_url(job, url, key, extra):
    params = dict(job.get("params", {}))
    params[job.get("keyparam", "serviceKey")] = key
    params.update(extra)
    q = urllib.parse.urlencode(params, safe="")
    return url + ("&" if "?" in url else "?") + q


def run_job(job, get=http_get, max_pages=200, log=print):
    key = os.environ.get(job["key"], "").strip()
    if not key:
        return {"상태": "키 없음", "키": job["key"]}
    rows, fields, used_url, note = [], set(), None, ""
    urls = job["url"] if isinstance(job.get("url"), list) else [job.get("url")]
    if job.get("kcisa_page"):
        u = fetch_kcisa_url(job["kcisa_page"], get)
        if not u:
            return {"상태": "호출 주소 못 찾음", "원천": job["src"]}
        urls = [u]
    years = [None]
    if job.get("year_param"):
        years = [str(TODAY.year - i) for i in range(0, 4)]
    for url in urls:
        for yr in years:
            rows, used_url = [], url
            pname, sname, size = job.get("page", ("pageNo", "numOfRows", 1000))
            for page in range(1, max_pages + 1):
                if job.get("seoul"):
                    full = url.format(key=urllib.parse.quote(key, safe=""), start=(page - 1) * size + 1, end=page * size)
                else:
                    extra = {pname: page, sname: size}
                    if yr:
                        extra[job["year_param"]] = yr
                    full = build_url(job, url, key, extra)
                try:
                    text = get(full)
                except Exception as e:  # noqa: BLE001
                    note = redact(str(e), key)[:200]
                    break
                try:
                    items = xml_items(text) if job.get("xml") or text.lstrip().startswith("<") else find_items(json.loads(text))
                    obj_total = None if job.get("xml") or text.lstrip().startswith("<") else total_count(json.loads(text))
                except Exception as e:  # noqa: BLE001
                    note = "응답 해석 실패: " + redact(text, key)[:200].replace("\n", " ")
                    break
                if not items:
                    if page == 1:
                        note = "빈 응답: " + redact(text, key)[:200].replace("\n", " ")
                    break
                rows += items
                for it in items:
                    fields.update(it.keys())
                if obj_total is not None and len(rows) >= obj_total:
                    break
                if len(items) < size:
                    break
                time.sleep(0.3)
            if rows:
                if yr:
                    note = f"{job['year_param']}={yr}"
                break
        if rows:
            break
    return {"상태": "성공" if rows else "실패", "rows": rows, "fields": sorted(fields), "url": used_url, "비고": note}


def save(job, res, root=ROOT, today=None):
    today = today or TODAY.isoformat()
    out = os.path.join(root, job["id"])
    os.makedirs(out, exist_ok=True)
    rows = res.get("rows", [])
    counts = {}
    for r in rows:
        sido, sgg = region_of(r)
        if job["kind"] == "series":
            num = next((r[k] for k in ("touNum", "visitrNum", "cnt") if k in r), 0)
            try:
                num = float(num)
            except (TypeError, ValueError):
                num = 0
        else:
            num = 1
        counts[(sido, sgg)] = counts.get((sido, sgg), 0) + num
    with open(os.path.join(out, "by_sigungu.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시도", "시군구", "값"])
        for (a, b), v in sorted(counts.items()):
            w.writerow([a, b, int(v) if float(v).is_integer() else round(v, 1)])
    kept = []
    if job["kind"] == "facility" and rows:
        cleaned = [clean_row(r) for r in rows]
        cols = sorted({k for r in cleaned for k in r})
        with open(os.path.join(out, "list.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(cleaned)
        kept = cols
    meta = {"자료": job["name"], "수집일": today, "상태": res["상태"], "건수": len(rows),
            "시군구수": len(counts), "원천": job["src"],
            "호출주소": (res.get("url") or "").split("?")[0].replace(os.environ.get(job["key"], "") or "\0", "***"),
            "남긴칸": kept, "버린칸": sorted(set(res.get("fields", [])) - set(kept)) if kept else [],
            "저장방식": {"facility": "공공시설 목록(전화·좌표·개인 칸 제외)+시군구 개수",
                       "business": "시군구별 개수만(업소 목록은 저장하지 않음)",
                       "series": "시군구별 합계"}[job["kind"]],
            "비고": res.get("비고", ""), "이용조건": LICENSE}
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "list"])
    ap.add_argument("--only", default="")
    ap.add_argument("--max-pages", type=int, default=200)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for j in JOBS:
            print(j["id"], j["name"], j["key"])
        return 0
    only = set(filter(None, a.only.split(",")))
    summary = {}
    for job in JOBS:
        if only and job["id"] not in only:
            continue
        try:
            res = run_job(job, max_pages=a.max_pages)
        except Exception as e:  # noqa: BLE001
            res = {"상태": "오류", "비고": redact(str(e), os.environ.get(job["key"], ""))[:200]}
        if res["상태"] == "키 없음":
            summary[job["id"]] = "키 없음"
            continue
        meta = save(job, res)
        summary[job["id"]] = f'{meta["상태"]} {meta["건수"]}건 {meta["비고"][:60]}'
        print(job["id"], summary[job["id"]], flush=True)
    os.makedirs(ROOT, exist_ok=True)
    with open(os.path.join(ROOT, "_check", "culture_api.json"), "w", encoding="utf-8") as f:
        json.dump({"수집일": TODAY.isoformat(), "결과": summary}, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
