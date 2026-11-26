"""등록된 인증키가 실제로 동작하는지 확인한다. 키 값은 어디에도 쓰지 않고, 있음/없음과 응답 상태만 남긴다.
사용: python3 collectors/key_check.py  → data/_check/keys.json
"""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request

D = (dt.date.today() - dt.timedelta(days=2)).strftime("%Y%m%d")
CHECKS = {
    "DATA_GO_KR_KEY": "https://api.data.go.kr/openapi/tn_pubr_public_lbrry_api?serviceKey={k}&type=json&pageNo=1&numOfRows=1",
    "KOPIS_KEY": "https://www.kopis.or.kr/openApi/restful/pblprfr?service={k}&stdate=" + D + "&eddate=" + D + "&cpage=1&rows=1",
    "KOBIS_KEY": "http://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json?key={k}&targetDt=" + D,
    "SEOUL_KEY": "http://openapi.seoul.go.kr:8088/{k}/json/SeoulPublicLibraryInfo/1/1/",
    "GG_KEY": "https://openapi.gg.go.kr/Tbggibllbrm?KEY={k}&Type=json&pIndex=1&pSize=1",
    "NL_KEY": "https://www.nl.go.kr/NL/search/openApi/search.do?key={k}&apiType=json&kwd=%EB%8F%84%EC%84%9C%EA%B4%80&pageSize=1&pageNum=1",
    "LAW_OC": "https://www.law.go.kr/DRF/lawSearch.do?OC={k}&target=ordin&type=JSON&query=%EB%8F%84%EC%84%9C%EA%B4%80&display=1",
}


PRESENCE_ONLY = ["KCISA_KEY", "KCISA_PRESS_KEY", "KCISA_BOOKSTORE_KEY", "KCISA_USEDBOOK_KEY",
                 "KCISA_POLICY_KR_KEY", "KCISA_POLICY_INTL_KEY", "KCISA_NLCY_RECO_KEY", "KCISA_NEWBOOK_KEY"]


def check(name, tmpl, get=None):
    k = os.environ.get(name, "").strip()
    if not k:
        return {"등록": False}
    url = tmpl.format(k=urllib.parse.quote(k, safe=""))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gov-lib-data"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(400).decode("utf-8", "replace")
            code = r.status
    except Exception as e:  # noqa: BLE001
        body, code = str(e), None
    body = body.replace(k, "***").replace(urllib.parse.quote(k, safe=""), "***")
    return {"등록": True, "HTTP": code, "응답앞부분": " ".join(body.split())[:200]}


def main():
    res = {"확인일": dt.date.today().isoformat(), "결과": {n: check(n, t) for n, t in CHECKS.items()}}
    for n in PRESENCE_ONLY:
        res["결과"][n] = {"등록": bool(os.environ.get(n, "").strip()), "비고": "등록 여부만 확인(호출 주소는 수집기에서)"}
    os.makedirs("data/_check", exist_ok=True)
    with open("data/_check/keys.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps({n: (v["등록"], v.get("HTTP")) for n, v in res["결과"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
