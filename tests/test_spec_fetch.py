import io, json, os, sys, tempfile, unittest, zipfile
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collectors"))
import spec_fetch as sf

PAGE = """<html><head><title>행정안전부_행정동별(성/연령) 주민등록 인구수 | 공공데이터 포털</title>
<meta property="og:title" content="행정안전부_행정동별(성/연령) 주민등록 인구수"></head><body>
<script>var swaggerUrl = "https://infuser.odcloud.kr/oas/docs?namespace=15108072/v1"; var x = "https://apis.data.go.kr/should/also/match";</script>
<table><tr><th>제공기관</th><td>행정안전부</td><th>관리부서명</th><td>주민과</td></tr>
<tr><th>관리부서 전화번호</th><td>044-205-1234</td><th>API 유형</th><td>REST</td></tr>
<tr><th>데이터포맷</th><td>JSON+XML</td><th>이용허락범위</th><td>이용허락범위 제한 없음</td></tr></table>
<p>End Point https://apis.data.go.kr/1741000/admmSexdAgePpltn</p>
<table><tr><th>항목명(국문)</th><th>항목명(영문)</th><th>항목구분</th><th>항목설명</th></tr>
<tr><td>서비스키</td><td>serviceKey</td><td>필수</td><td>인증키</td></tr>
<tr><td>조회시작년월</td><td>srchFrYm</td><td>필수</td><td>문의 담당 hong@example.go.kr</td></tr></table>
<table><tr><th>에러메시지</th><th>에러코드</th></tr><tr><td>X</td><td>01</td></tr></table>
<a href="/cmm/cmm/fileDownload.do?atchFileId=FILE_000000000000001&amp;fileDetailSn=1">활용가이드.docx</a>
<a href="/cmm/cmm/fileDownload.do?atchFileId=FILE_000000000000002&amp;fileDetailSn=1">설명.hwp</a>
</body></html>"""


def make_docx(tables):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = ""
    for rows in tables:
        body += "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in r) + "</w:tr>" for r in rows) + "</w:tbl>"
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


SWAGGER = {"swagger": "2.0", "host": "api.odcloud.kr", "basePath": "/api",
           "paths": {"/15108072/v1/uddi:abc": {"get": {"summary": "인구", "parameters": [
               {"name": "page", "in": "query", "required": False, "description": "쪽"},
               {"name": "serviceKey", "in": "query", "required": True, "description": "인증키"}],
               "responses": {"200": {"schema": {"$ref": "#/definitions/Res"}}}}}},
           "definitions": {"Res": {"type": "object", "properties": {"data": {"type": "array", "items": {"$ref": "#/definitions/Row"}}}},
                           "Row": {"type": "object", "properties": {"admmCd": {"description": "행정동코드"}, "totNmprCnt": {"description": "총인구"}}}}}


class TSpec(unittest.TestCase):
    def test_parse_page(self):
        p = sf.parse_page(PAGE)
        self.assertEqual(p["제목"], "행정안전부_행정동별(성/연령) 주민등록 인구수")
        self.assertIn("https://apis.data.go.kr/1741000/admmSexdAgePpltn", p["주소 후보"])
        self.assertEqual(p["명세 주소"], ["https://infuser.odcloud.kr/oas/docs?namespace=15108072/v1"])
        self.assertEqual(len(p["참고문서 주소"]), 2)
        self.assertTrue(all(u.startswith("https://www.data.go.kr/cmm/cmm/fileDownload.do?") and "&amp;" not in u for u in p["참고문서 주소"]))
        self.assertEqual(len(p["표"]), 1)                                   # 항목 표만(에러 표·정보 표는 뺌)
        self.assertEqual(p["표"][0]["머리"][1], "항목명(영문)")
        self.assertEqual([r[1] for r in p["표"][0]["줄"]], ["serviceKey", "srchFrYm"])
        self.assertEqual(p["항목"]["제공기관"], "행정안전부")
        self.assertEqual(p["항목"]["API 유형"], "REST")
        self.assertIn("제한 없음", p["항목"]["이용허락범위"])
        dump = json.dumps(p, ensure_ascii=False)
        self.assertNotIn("044-205-1234", dump)                            # 전화·이메일은 남기지 않음
        self.assertNotIn("hong@example.go.kr", dump)
        self.assertNotIn("관리부서 전화번호", p["항목"])
        f = sf.page_fields("분류체계 문화체육관광 - 문화예술 제공기관 행정안전부 관리부서명 지역디지털협력과 관리부서 전화번호 044-000-0000\nAPI 유형 REST 데이터포맷 JSON+XML")
        self.assertEqual((f["제공기관"], f["관리부서명"], f["API 유형"], f["데이터포맷"]), ("행정안전부", "지역디지털협력과", "REST", "JSON+XML"))
        self.assertNotIn("044-000-0000", json.dumps(f, ensure_ascii=False))
        g = sf.page_fields("제공기관 행정안전부\n데이터 유형 오픈API 데이터 형식 JSON+XML\n이용허락범위 이용허락범위 제한 없음")   # 2026-11-26 실제 화면 이름표
        self.assertEqual((g["데이터 유형"], g["데이터 형식"], g["이용허락범위"]), ("오픈API", "JSON+XML", "제한 없음"))

    def test_docx_tables_and_kind(self):
        data = make_docx([[["항목명(영문)", "항목명(국문)", "항목구분"], ["admmCd", "행정동코드", "필수"]],
                          [["개정 이력", "날짜"], ["1.0", "2024-01-01"]]])
        self.assertEqual(sf.doc_kind(data), "docx")
        t = sf.docx_tables(data)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["줄"], [["admmCd", "행정동코드", "필수"]])
        self.assertEqual(sf.doc_kind(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest"), "hwp·doc")
        self.assertEqual(sf.doc_kind(b"%PDF-1.7"), "pdf")

    def test_swagger_summary(self):
        s = sf.swagger_summary(SWAGGER)
        self.assertEqual(s["주소"], "api.odcloud.kr/api")
        op = s["경로"][0]
        self.assertEqual(op["방식"], "GET")
        self.assertEqual([(r["이름"], r["필수"]) for r in op["요청"]], [("page", False), ("serviceKey", True)])
        self.assertEqual([r["이름"] for r in op["응답"]], ["data", "admmCd", "totNmprCnt"])

    def test_run_writes_spec_and_index(self):
        docx = make_docx([[["항목명(영문)", "항목설명"], ["srchToYm", "조회종료년월"]]])

        def get(url):
            if "openapi.do" in url and "15108072" in url:
                return PAGE.encode()
            if "openapi.do" in url:
                raise OSError("HTTP Error 404")
            if "infuser" in url:
                return json.dumps(SWAGGER).encode()
            if "FILE_000000000000001" in url:
                return docx
            return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1hwp"
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "catalog"))
            with open(os.path.join(d, sf.CANDIDATES), "w", encoding="utf-8") as f:
                json.dump({"candidates": [{"pk": "15108072", "이름": "인구"}, {"pk": "99999999", "이름": "없는 것"}]}, f)
            with mock.patch.object(sf.time, "sleep"):
                idx = sf.run(root=d, get=get, delay=0, today="2026-11-26", log=lambda *a: None)
            self.assertEqual([(x["pk"], x["상태"]) for x in idx], [("15108072", "성공"), ("99999999", "실패")])
            with open(os.path.join(d, sf.OUT, "15108072.json"), encoding="utf-8") as f:
                spec = json.load(f)
            for k in ("원천", "수집일", "이용조건", "이용허락범위"):
                self.assertTrue(spec[k], k)                                 # 원천 주소·수집일·이용조건(CLAUDE.md)
            self.assertEqual(spec["원천"], "https://www.data.go.kr/data/15108072/openapi.do")
            self.assertEqual(spec["명세"][0]["경로"][0]["요청"][1]["이름"], "serviceKey")
            kinds = {x["종류"] for x in spec["참고문서"]}
            self.assertEqual(kinds, {"docx", "hwp·doc"})
            self.assertEqual(next(x for x in spec["참고문서"] if x["종류"] == "docx")["표"][0]["줄"], [["srchToYm", "조회종료년월"]])
            with open(os.path.join(d, sf.OUT, "15108072.json"), encoding="utf-8") as f:
                raw = f.read()
            self.assertNotIn("044-205", raw)
            with open(os.path.join(d, sf.OUT, "99999999.json"), encoding="utf-8") as f:
                bad = json.load(f)
            self.assertIn("404", " ".join(bad["비고"]))
            with open(os.path.join(d, sf.OUT, "index.json"), encoding="utf-8") as f:
                index = json.load(f)
            self.assertEqual(len(index["목록"]), 2)
            with mock.patch.object(sf.time, "sleep"):                        # 일부만 다시 받아도 목록의 다른 줄은 남음
                sf.run(root=d, only=["99999999"], get=get, delay=0, today="2026-11-27", log=lambda *a: None)
            with open(os.path.join(d, sf.OUT, "index.json"), encoding="utf-8") as f:
                index = json.load(f)
            self.assertEqual([x["pk"] for x in index["목록"]], ["15108072", "99999999"])

    def test_embedded_specs(self):
        raw = ('<script>var spec = {"swagger":"2.0","paths":{"/a":{"get":{"parameters":[{"name":"admmCd","required":true,"in":"query","description":"행정기관코드"}],'
               '"responses":{"200":{"schema":{"type":"object","properties":{"totNmprCnt":{"description":"총인구","properties":{}}}}}}}}}};'
               'var v = {"swaggerOprtinVOs":[{"oprtinUrl":"https://rdoa.jumin.go.kr/x/selectX","oprtinNm":"목록 조회","reqList":'
               '[{"paramtrNm":"srchFrYm","paramtrDivision":"필수","paramtrDc":"조회시작년월 문의 02-123-4567"}]}]};</script>')
        specs, ops = sf.embedded_specs(raw)
        self.assertEqual(len(specs), 1)
        s = sf.swagger_summary(specs[0])
        self.assertEqual(s["경로"][0]["요청"][0]["이름"], "admmCd")
        self.assertEqual(s["경로"][0]["응답"][0]["이름"], "totNmprCnt")
        self.assertEqual((ops[0]["주소"], ops[0]["요청"][0]["이름"], ops[0]["요청"][0]["구분"]), ("https://rdoa.jumin.go.kr/x/selectX", "srchFrYm", "필수"))
        self.assertNotIn("02-123-4567", json.dumps(ops, ensure_ascii=False))
        r = sf.fetch_one("1", get=lambda u: raw.encode(), delay=0, today="2026-11-26")
        self.assertEqual(r["상태"], "성공")

    def test_candidates_file(self):
        with open(os.path.join(os.path.dirname(__file__), "..", sf.CANDIDATES), encoding="utf-8") as f:
            c = json.load(f)["candidates"]
        pks = [x["pk"] for x in c]
        self.assertEqual(len(pks), len(set(pks)))
        self.assertTrue(all(p.isdigit() for p in pks))


if __name__ == "__main__":
    unittest.main()
