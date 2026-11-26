"""법제처 국가법령정보 OPEN API로 자치법규 목록의 최신성을 확인한다.

legalize-kr 변환본(data/ordinance/index.csv)에 아직 없는, 법제처에 새로 올라온 도서관·독서 자치법규를 찾아
data/ordinance/freshness.json 에 남긴다. 인증키는 환경변수 LAW_OC 로만 쓰고 어디에도 적지 않는다.
사용: LAW_OC=... python3 collectors/law_check.py run
"""
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from ordinance_lib import KEYWORDS, is_library  # noqa: E402

URL = "https://www.law.go.kr/DRF/lawSearch.do"
QUERIES = ("도서관", "독서", "북스타트", "서점", "문학관")


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "gov-lib-data"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def items_of(obj):
    """응답 JSON 안에서 '자치법규ID'를 가진 항목 목록을 찾는다(응답 구조가 바뀌어도 버티도록)."""
    if isinstance(obj, dict):
        if "자치법규ID" in obj:
            return [obj]
        out = []
        for v in obj.values():
            out += items_of(v)
        return out
    if isinstance(obj, list):
        out = []
        for v in obj:
            out += items_of(v)
        return out
    return []


def search(oc, query, get=http_get, page_size=100, max_pages=30):
    found = []
    for page in range(1, max_pages + 1):
        q = urllib.parse.urlencode({"OC": oc, "target": "ordin", "type": "JSON", "query": query,
                                    "display": page_size, "page": page})
        text = get(URL + "?" + q)
        try:
            data = json.loads(text)
        except ValueError:
            raise RuntimeError("JSON이 아닌 응답(키·접속 차단 확인 필요): " + text[:120].replace(oc, "***"))
        items = items_of(data)
        found += items
        if len(items) < page_size:
            break
        time.sleep(0.5)
    return found


def compare(remote, known_ids):
    new = []
    seen = set()
    for it in remote:
        rid = str(it.get("자치법규ID", "")).strip()
        name = str(it.get("자치법규명", ""))
        if not rid or rid in seen or not is_library(name):
            continue
        seen.add(rid)
        if rid not in known_ids:
            new.append({"자치법규ID": rid, "자치법규명": name, "지자체기관명": it.get("지자체기관명", ""),
                        "공포일자": it.get("공포일자", ""), "자치법규종류": it.get("자치법규종류", "")})
    return sorted(new, key=lambda x: x["공포일자"], reverse=True), len(seen)


def run(out="data/ordinance", get=http_get, today=None):
    oc = os.environ.get("LAW_OC", "").strip()
    if not oc:
        raise SystemExit("LAW_OC 가 없습니다(Secrets 등록 확인)")
    with open(os.path.join(out, "index.csv"), encoding="utf-8-sig") as f:
        known = {r["자치법규ID"] for r in csv.DictReader(f) if r.get("자치법규ID")}
    remote = []
    for q in QUERIES:
        remote += search(oc, q, get)
    new, total = compare(remote, known)
    res = {"확인일": today or dt.date.today().isoformat(), "법제처_검색건수": total,
           "우리_목록건수": len(known), "법제처에만_있음": new,
           "검색어": list(QUERIES), "원천": "국가법령정보 OPEN API https://open.law.go.kr",
           "주의": "법제처 검색은 이름 검색이라 우리 검색어(" + ",".join(KEYWORDS) + ")와 범위가 조금 다를 수 있다"}
    with open(os.path.join(out, "freshness.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: res[k] for k in ("법제처_검색건수", "우리_목록건수")} | {"새것": len(new)}, ensure_ascii=False))
    return res


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        run()
    else:
        print(__doc__)
