"""공공데이터포털 오픈API 명세 모으기(spec-fetch).

새 API의 호출 주소·요청 변수·응답 항목을 사람이 상세 화면에서 옮겨 적지 않도록, 상세 페이지(openapi.do)와
그 페이지가 가리키는 명세(swagger JSON)·참고문서(.docx)를 읽어 catalog/specs/<pk>.json 에 정리한다.
매개변수 이름을 짐작해서 수집기를 짜다 실패하는 일(2026-11 GG_KEY 서비스 이름 짐작)을 막으려는 것이다.

원칙(CLAUDE.md):
- 공개 페이지와 원천 파일 읽기만 한다. 받은 파일은 글자·표로만 읽고 실행하지 않는다.
- 인증키를 쓰지 않는다(명세 화면은 키 없이 열린다).
- 원본 파일은 저장하지 않는다. 명세의 항목 이름·설명·주소만 남긴다(문서 본문을 옮겨 싣지 않음).
- 전화번호·이메일은 가린다.

사용: python3 collectors/spec_fetch.py run [--only 15108072,15156652] [--delay 1.5]
"""
import argparse
import datetime as dt
import html
import io
import json
import os
import re
import sys
import time
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

PAGE = "https://www.data.go.kr/data/{pk}/openapi.do"
CANDIDATES = "catalog/candidates.json"
OUT = "catalog/specs"
UA = {"User-Agent": "gov-lib-data spec-fetch (+https://github.com/altarry-prog/gov-lib-data)"}
MAX_BYTES = 20 * 1024 * 1024          # 받는 파일 한도
MAX_XML = 50 * 1024 * 1024            # docx 안 document.xml 풀린 크기 한도
MAX_ROWS = 300
LICENSE = "공공데이터포털 상세 화면의 이용허락범위를 따른다(아래 '이용허락범위' 칸). 이 파일은 명세 항목 이름·설명만 옮긴 것"

PHONE = re.compile(r"\b0\d{1,2}[-.)\s]\d{3,4}[-.\s]\d{4}\b|\b1\d{3}-\d{4}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
ENDPOINT = re.compile(r"https?://(?:apis\.data\.go\.kr|api\.odcloud\.kr/api)/[0-9A-Za-z_./-]+")
SWAGGER = re.compile(r"https?://infuser\.odcloud\.kr/[^\s\"'<>]+|[\"'](https?://[^\"'\s<>]*swagger[^\"'\s<>]*\.json)[\"']", re.I)
DOCS = re.compile(r"(?:https?://www\.data\.go\.kr)?/cmm/cmm/fileDownload\.do\?[^\s\"'<>]*atchFileId=[0-9A-Za-z_]+[^\s\"'<>]*")
LABELS = ["분류체계", "제공기관", "관리부서명", "관리부서 전화번호", "API 유형", "데이터 유형", "데이터포맷", "데이터 형식", "활용신청", "키워드", "등록일", "수정일",
          "비용부과유무", "비용부과기준 및 단위", "이용허락범위", "트래픽", "심의유형", "업데이트 주기", "차기 등록 예정일"]
KEEP_HEAD = re.compile(r"항목명|항목\s*설명|요청\s*변수|출력\s*결과|parameter|element|변수명", re.I)


def mask(text):
    return EMAIL.sub("[가림]", PHONE.sub("[가림]", text or ""))


def clean(text):
    return mask(re.sub(r"\s+", " ", html.unescape(text or "")).strip())


class Tables(HTMLParser):
    """HTML 안 표를 [{"머리": [...], "줄": [[...]]}] 로. 표 밖 글자는 text 에 모은다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.text, self.skip, self.pairs = [], [], [], 0, {}

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "table":
            self.stack.append({"머리": [], "줄": [], "row": None, "cell": None, "th": False})
        elif self.stack and tag == "tr":
            self.stack[-1]["row"] = []
        elif self.stack and tag in ("td", "th"):
            t = self.stack[-1]
            t["cell"], t["th"] = [], tag == "th"
            self.text.append(" ")
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "dt", "dd"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif not self.stack:
            return
        elif tag in ("td", "th"):
            t = self.stack[-1]
            if t["cell"] is not None and t["row"] is not None:
                t["row"].append((clean("".join(t["cell"])), t["th"]))
            t["cell"] = None
            self.text.append(" ")
        elif tag == "tr":
            t = self.stack[-1]
            row = t["row"] or []
            for (a, ath), (b, bth) in zip(row, row[1:]):       # <th>이름</th><td>값</td> 짝(제공기관·이용허락범위 등)
                if ath and not bth and a and a not in self.pairs:
                    self.pairs[a] = b
            if row and all(th for _, th in row) and not t["머리"]:
                t["머리"] = [c for c, _ in row]
            elif row:
                t["줄"].append([c for c, _ in row])
            t["row"] = None
        elif tag == "table":
            t = self.stack.pop()
            if t["머리"] or t["줄"]:
                self.tables.append({"머리": t["머리"], "줄": t["줄"][:MAX_ROWS]})

    def handle_data(self, data):
        if self.skip:
            return
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(data)
        self.text.append(data)


def keep_table(t):
    head = " ".join(t["머리"] or (t["줄"][0] if t["줄"] else []))
    return bool(KEEP_HEAD.search(head))


def page_fields(text, pairs=None):
    """표의 <th>이름</th><td>값</td> 짝을 먼저 쓰고, 없으면 글자에서 '이름표 값' 을 다음 이름표·줄바꿈 앞까지."""
    pairs = pairs or {}
    flat = re.sub(r"[ \t\r\f\v]+", " ", text)
    out = {}
    for lab in LABELS:
        if lab == "관리부서 전화번호":
            continue
        v = pairs.get(lab)
        if v is None:
            others = "|".join(re.escape(x) for x in LABELS if x != lab)
            m = re.search(re.escape(lab) + r" ?\n? ?([^\n]{1,80}?) ?(?=" + others + r"|\n|$)", flat)
            v = m.group(1) if m else None
        v = re.sub("^" + re.escape(lab) + r"\s*", "", (v or "").strip())
        if v:
            out[lab] = mask(v)
    return out


def parse_page(raw):
    p = Tables()
    p.feed(raw)
    text = "".join(p.text)
    og = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', raw)
    title = clean(og.group(1)) if og else clean((re.search(r"<title>(.*?)</title>", raw, re.S) or [None, ""])[1])
    return {
        "제목": re.sub(r"\s*\|\s*공공데이터\s*포털\s*$", "", title),
        "항목": page_fields(text, p.pairs),
        "주소 후보": sorted(set(u.rstrip("/.") for u in ENDPOINT.findall(raw))),
        "명세 주소": sorted(set(html.unescape(a or b) for a, b in
                            [(m.group(0) if not m.group(1) else "", m.group(1) or "") for m in SWAGGER.finditer(raw)])),
        "참고문서 주소": sorted(set("https://www.data.go.kr" + d if d.startswith("/") else d for d in
                              (html.unescape(x) for x in DOCS.findall(raw)))),
        "표": [t for t in p.tables if keep_table(t)][:30],
    }


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_tables(data):
    """docx(zip) 안 word/document.xml 의 표만 글자로. 실행하는 것은 없다."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > MAX_XML:
            raise ValueError("document.xml 이 너무 큼")
        root = ET.fromstring(z.read(info))
    out = []
    for tbl in root.iter(W + "tbl"):
        rows = []
        for tr in tbl.iter(W + "tr"):
            rows.append([clean("".join(t.text or "" for t in tc.iter(W + "t"))) for tc in tr.iter(W + "tc")])
        if rows:
            t = {"머리": rows[0], "줄": rows[1:MAX_ROWS + 1]}
            if keep_table(t):
                out.append(t)
    return out[:30]


def doc_kind(data):
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "docx" if "word/document.xml" in z.namelist() else ("hwpx" if any(n.startswith("Contents/") for n in z.namelist()) else "zip")
        except zipfile.BadZipFile:
            return "기타"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "hwp·doc"
    if data[:5] == b"%PDF-":
        return "pdf"
    return "기타"


def swagger_summary(obj):
    """swagger/OpenAPI JSON → 경로별 요청 변수·응답 항목 이름과 설명만."""
    out = []
    defs = obj.get("definitions") or (obj.get("components") or {}).get("schemas") or {}

    def props(schema, depth=0):
        if not isinstance(schema, dict) or depth > 4:
            return []
        if "$ref" in schema:
            return props(defs.get(schema["$ref"].split("/")[-1], {}), depth + 1)
        if schema.get("type") == "array":
            return props(schema.get("items", {}), depth + 1)
        res = []
        for k, v in (schema.get("properties") or {}).items():
            res.append({"이름": k, "설명": clean((v or {}).get("description", ""))})
            res += props(v, depth + 1)
        return res

    for path, ops in (obj.get("paths") or {}).items():
        for method, op in (ops or {}).items():
            if not isinstance(op, dict):
                continue
            req = [{"이름": p.get("name"), "필수": bool(p.get("required")), "위치": p.get("in"), "설명": clean(p.get("description", ""))}
                   for p in op.get("parameters", []) if isinstance(p, dict)]
            resp = []
            for code, r in (op.get("responses") or {}).items():
                if str(code).startswith("2") and isinstance(r, dict):
                    sch = r.get("schema") or next(iter(((r.get("content") or {}).get("application/json") or {}).values()), {})
                    if isinstance(sch, dict) and "schema" in sch:
                        sch = sch["schema"]
                    resp += props(sch)
            out.append({"경로": path, "방식": method.upper(), "요약": clean(op.get("summary", "")), "요청": req, "응답": resp[:MAX_ROWS]})
    return {"주소": (obj.get("host", "") + obj.get("basePath", "")) or ",".join(s.get("url", "") for s in obj.get("servers", []) if isinstance(s, dict)),
            "경로": out[:50]}


def http_get(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("파일이 너무 큼")
    return data


def fetch_one(pk, get=http_get, delay=1.5, today=None, dump=None):
    today = today or dt.date.today().isoformat()
    src = PAGE.format(pk=pk)
    res = {"pk": str(pk), "원천": src, "수집일": today, "이용조건": LICENSE, "상태": "실패", "비고": []}
    try:
        raw = get(src).decode("utf-8", "replace")
        if dump:  # 화면 구조 확인용(Actions 실행 결과물로만 올리고 커밋하지 않음)
            os.makedirs(dump, exist_ok=True)
            with open(os.path.join(dump, f"{pk}.html"), "w", encoding="utf-8") as f:
                f.write(raw)
        page = parse_page(raw)
    except Exception as e:  # noqa: BLE001
        res["비고"].append("상세 화면을 못 읽음: " + mask(str(e))[:160])
        return res
    res.update({k: v for k, v in page.items() if k != "참고문서 주소"})
    res["이용허락범위"] = page["항목"].get("이용허락범위", "화면에서 못 찾음")
    specs, docs = [], []
    for u in page["명세 주소"][:3]:
        time.sleep(delay)
        try:
            specs.append({"주소": u, **swagger_summary(json.loads(get(u).decode("utf-8", "replace")))})
        except Exception as e:  # noqa: BLE001
            res["비고"].append(f"명세 못 읽음({u[:80]}): " + mask(str(e))[:120])
    for u in page["참고문서 주소"][:4]:
        time.sleep(delay)
        try:
            data = get(u)
            kind = doc_kind(data)
            d = {"주소": u, "종류": kind}
            if kind == "docx":
                d["표"] = docx_tables(data)
            else:
                d["비고"] = "표 자동 추출은 docx 만. 사람이 확인"
            docs.append(d)
        except Exception as e:  # noqa: BLE001
            docs.append({"주소": u, "종류": "못 받음", "비고": mask(str(e))[:120]})
    res["명세"], res["참고문서"] = specs, docs
    got_params = bool(page["표"]) or any(s["경로"] for s in specs) or any(d.get("표") for d in docs)
    res["상태"] = "성공" if (page["주소 후보"] or specs) and got_params else ("일부" if (page["제목"] or page["표"]) else "실패")
    if not page["주소 후보"] and not specs:
        res["비고"].append("호출 주소를 화면에서 못 찾음(파일데이터·표준데이터 화면일 수 있음)")
    return res


def run(root=".", only=None, get=http_get, delay=1.5, today=None, log=print, dump=None):
    with open(os.path.join(root, CANDIDATES), encoding="utf-8") as f:
        cands = json.load(f)["candidates"]
    if only:
        want = {x.strip() for x in only if x.strip()}
        cands = [c for c in cands if str(c["pk"]) in want]
    out_dir = os.path.join(root, OUT)
    os.makedirs(out_dir, exist_ok=True)
    index = []
    for i, c in enumerate(cands):
        if i:
            time.sleep(delay)
        r = fetch_one(c["pk"], get=get, delay=delay, today=today, dump=dump)
        r["후보 이름"], r["쓰는 곳"] = c.get("이름", ""), c.get("쓰는 곳", "")
        with open(os.path.join(out_dir, f"{c['pk']}.json"), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        index.append({"pk": r["pk"], "제목": r.get("제목", ""), "후보 이름": r["후보 이름"], "상태": r["상태"],
                      "주소 후보": len(r.get("주소 후보", [])), "표": len(r.get("표", [])), "명세": len(r.get("명세", [])),
                      "참고문서": len(r.get("참고문서", [])), "원천": r["원천"], "수집일": r["수집일"]})
        log(f"{c['pk']} {r['상태']} {r.get('제목', '')[:40]}")
    old = {}
    p = os.path.join(out_dir, "index.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            old = {x["pk"]: x for x in json.load(f).get("목록", [])}
    old.update({x["pk"]: x for x in index})
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"설명": "공공데이터포털 오픈API 명세 요약(collectors/spec_fetch.py). 원천 화면·이용조건은 각 파일에", "이용조건": LICENSE,
                   "목록": sorted(old.values(), key=lambda x: x["pk"])}, f, ensure_ascii=False, indent=1)
    return index


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--only", default="")
    r.add_argument("--delay", type=float, default=1.5)
    r.add_argument("--dump", default="", help="원본 화면을 이 폴더에 저장(커밋 금지, Actions 결과물로만)")
    a = ap.parse_args(argv)
    idx = run(only=[x for x in a.only.split(",") if x.strip()] or None, delay=a.delay, dump=a.dump or None)
    bad = [x for x in idx if x["상태"] == "실패"]
    print(f"명세 {len(idx)}건 (성공 {sum(x['상태'] == '성공' for x in idx)}, 일부 {sum(x['상태'] == '일부' for x in idx)}, 실패 {len(bad)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
