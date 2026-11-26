import json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collectors"))
import api_catalog as ac

PATH = "/15062804/v1/uddi:aaaa-1111"
PAGE = '<script>var s={"swagger":"2.0","paths":{"%s":{"get":{"parameters":[]}}}};</script>' % PATH


def row(i, name, kw="", org="기관A", typ="오픈API", cat="문화관광 - 문화예술", used=10, lic="이용허락범위 제한 없음"):
    return {"목록키": str(15000000 + i), "목록유형": typ, "목록명": name, "키워드": kw, "설명": "", "분류체계": cat, "제공기관": org,
            "관리부서 전화번호": "02-123-4567", "다운로드_활용신청건수": used, "조회수": 100, "이용허락범위": lic}


ROWS = ([row(i, f"공연 정보 {i}", org=f"예술기관{i % 3}") for i in range(20)] +
        [row(100 + i, f"도서관 대출 {i}", org=f"도서관기관{i}") for i in range(5)] +
        [row(200, "미세먼지 예보", kw="대기"), row(201, "문화 축제 파일", typ="파일데이터"),
         row(202, "행정동별 주민등록 인구", cat="일반공공행정 - 일반행정", org="행정안전부")])


class TCatalog(unittest.TestCase):
    def get(self, url):
        if url.startswith(ac.SRC):
            return PAGE.encode()
        page = int(url.split("page=")[1].split("&")[0])
        chunk = ROWS[(page - 1) * 10: page * 10]
        return json.dumps({"totalCount": len(ROWS), "data": chunk}).encode()

    def test_find_path(self):
        self.assertEqual(ac.find_path(PAGE), PATH)

    def test_fallback_to_infuser(self):
        def get(u):
            if "infuser" in u:
                return json.dumps({"swagger": "2.0", "paths": {PATH: {"get": {}}}}).encode()
            if u.startswith(ac.SRC):
                return b"<html>no spec</html>"
            return json.dumps({"totalCount": 1, "data": [row(1, "공연")]}).encode()
        with mock.patch.object(ac.time, "sleep"):
            rows, total, path = ac.fetch_rows("K", get=get, log=lambda *a: None)
        self.assertEqual((path, total, len(rows)), (PATH, 1, 1))

    def test_run(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "catalog"))
            with open(os.path.join(d, "catalog/candidates.json"), "w", encoding="utf-8") as f:
                json.dump({"candidates": [{"pk": "15000100", "이름": "이미"}]}, f)
            with mock.patch.object(ac.time, "sleep"), mock.patch.object(ac, "fetch_rows", wraps=ac.fetch_rows) as fr:
                out, meta = ac.run(root=d, key="SECRETKEY", get=lambda u: self.get(u.replace("perPage=1000", "perPage=10")),
                                   top=10, per_org=2, to_candidates=True, today="2026-11-27", log=lambda *a: None)
            names = [x["목록명"] for x in out]
            self.assertNotIn("미세먼지 예보", names)                       # 빼는 분야
            self.assertNotIn("문화 축제 파일", names)                      # 파일데이터는 뺌
            self.assertLessEqual(max(sum(x["제공기관"] == o for x in out) for o in {x["제공기관"] for x in out}), 2)   # 기관당 한도
            self.assertTrue(any("도서관" in n for n in names) and any("인구" in n for n in names))   # 분야 폭
            self.assertEqual(len(out), 10)
            raw = open(os.path.join(d, ac.OUT_CSV), encoding="utf-8-sig").read()
            self.assertNotIn("02-123-4567", raw)
            self.assertNotIn("SECRETKEY", raw + json.dumps(meta, ensure_ascii=False))
            self.assertEqual((meta["전체 목록"], meta["원천"], meta["수집일"]), (len(ROWS), ac.SRC, "2026-11-27"))
            with open(os.path.join(d, "catalog/candidates.json"), encoding="utf-8") as f:
                pks = [x["pk"] for x in json.load(f)["candidates"]]
            self.assertEqual(len(pks), len(set(pks)))
            self.assertGreater(len(pks), 5)


if __name__ == "__main__":
    unittest.main()
