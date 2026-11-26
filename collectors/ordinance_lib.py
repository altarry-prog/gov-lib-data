"""전국 지자체 도서관·독서 관련 자치법규 목록과 본문 스냅샷.

원천: 국가법령정보센터 자치법규(공공저작물). 변환본 legalize-kr/ordinance-kr(마크다운, 구조·메타데이터 MIT)을 읽는다.
경로 규칙: {광역}/{기초|_본청|_교육청}/{종류}/{자치법규명}/본문.md
원천 저장소는 이력을 다시 쓸 수 있으므로(force-push) 커밋 번호 대신 자치법규ID·일련번호·공포일자를 남긴다.

읽기만 한다: GitHub git trees API(폴더 목록)와 raw 파일. 외부 코드는 받지도 실행하지도 않는다.
사용: python3 collectors/ordinance_lib.py run [--out data/ordinance] [--limit-regions N]
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

OWNER_REPO = "legalize-kr/ordinance-kr"
BRANCH = "main"
API = "https://api.github.com/repos/" + OWNER_REPO
RAW = "https://raw.githubusercontent.com/" + OWNER_REPO + "/" + BRANCH + "/"
KINDS = ("조례", "규칙")
# 이름에 이 말이 들어가면 도서관·독서 관련으로 본다. "책"만으로는 "정책"이 걸리므로 쓰지 않는다.
KEYWORDS = ("도서관", "독서", "책읽", "책 읽", "책 문화", "책문화", "북스타트", "서점", "문학관", "인문도시", "인문학", "출판문화")
FIELDS = ["광역", "기초", "종류", "자치법규명", "자치법규ID", "자치법규일련번호", "공포일자", "공포번호",
          "시행일자", "제개정구분", "자치법규분야", "담당부서", "원천주소", "경로"]
FM_KEYS = ["자치법규ID", "자치법규일련번호", "공포일자", "공포번호", "시행일자", "제개정구분", "자치법규분야", "담당부서"]


def http_get(url, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "gov-lib-data"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok and url.startswith("https://api.github.com/"):
        req.add_header("Authorization", "Bearer " + tok)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def tree(sha_or_ref, get=http_get):
    """폴더 하나의 항목(재귀 아님). 1,000개 제한이 있는 contents API 대신 trees API."""
    data = json.loads(get(API + "/git/trees/" + urllib.parse.quote(sha_or_ref, safe="")))
    return [(t["path"], t["type"], t["sha"]) for t in data.get("tree", [])]


def is_library(name):
    return any(k in name for k in KEYWORDS)


def parse_frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    out = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def law_url(fm, name):
    seq = fm.get("자치법규일련번호")
    if seq:
        return "https://www.law.go.kr/ordinInfoP.do?ordinSeq=" + seq
    return "https://www.law.go.kr/자치법규/" + urllib.parse.quote(name)


def find(get=http_get, limit_regions=None, log=print):
    """(광역, 기초, 종류, 폴더이름, 경로) 목록."""
    found = []
    regions = [t for t in tree(BRANCH, get) if t[1] == "tree" and not t[0].startswith(".")]
    if limit_regions:
        regions = regions[:limit_regions]
    for rname, _, rsha in regions:
        for bname, btype, bsha in tree(rsha, get):
            if btype != "tree":
                continue
            for kname, ktype, ksha in tree(bsha, get):
                if ktype != "tree" or kname not in KINDS:
                    continue
                for lname, ltype, _ in tree(ksha, get):
                    if ltype == "tree" and is_library(lname):
                        found.append((rname, bname, kname, lname, f"{rname}/{bname}/{kname}/{lname}"))
        log(f"{rname}: 누적 {len(found)}건")
    return found


def collect(out, get=http_get, limit_regions=None, today=None, log=print):
    today = today or dt.date.today().isoformat()
    rows = []
    for rname, bname, kname, lname, path in find(get, limit_regions, log):
        text = get(RAW + urllib.parse.quote(path + "/본문.md"), accept="text/plain")
        fm = parse_frontmatter(text)
        name = fm.get("자치법규명") or re.sub(r"_[0-9]+$", "", lname)
        row = {"광역": rname, "기초": bname, "종류": kname, "자치법규명": name,
               "원천주소": law_url(fm, name), "경로": path}
        row.update({k: fm.get(k, "") for k in FM_KEYS})
        rows.append(row)
        snap = os.path.join(out, "text", rname, bname, kname, lname + ".md")
        os.makedirs(os.path.dirname(snap), exist_ok=True)
        with open(snap, "w", encoding="utf-8") as f:
            f.write(text)
    rows.sort(key=lambda r: (r["광역"], r["기초"], r["종류"], r["자치법규명"]))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "index.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    by_region = {}
    for r in rows:
        by_region[r["광역"]] = by_region.get(r["광역"], 0) + 1
    meta = {
        "자료": "전국 지자체 도서관·독서 관련 자치법규 목록",
        "수집일": today,
        "건수": len(rows),
        "광역별": by_region,
        "검색어": list(KEYWORDS),
        "종류": list(KINDS),
        "원천": "국가법령정보센터 자치법규 https://www.law.go.kr",
        "변환본": "https://github.com/" + OWNER_REPO,
        "이용조건": "자치법규 원문은 공공저작물. 변환본 구조·메타데이터 MIT",
        "주의": "이름에 검색어가 든 것만 모았다. 이름에 없지만 도서관 조항을 가진 조례(예: 문화예술 진흥 조례)는 빠진다.",
        "부분수집": bool(limit_regions),
    }
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return rows, meta


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--out", default="data/ordinance")
    ap.add_argument("--limit-regions", type=int, default=None)
    a = ap.parse_args(argv)
    rows, meta = collect(a.out, limit_regions=a.limit_regions)
    print(json.dumps({"건수": meta["건수"], "광역별": meta["광역별"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
