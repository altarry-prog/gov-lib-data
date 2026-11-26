import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collectors"))
import culture_api as ca


class T(unittest.TestCase):
    def test_find_items_and_region(self):
        obj = {"response": {"body": {"totalCount": 2, "items": {"item": [
            {"facNm": "가도서관", "ROAD_NM_ADDR": "경기도 부천시 원미구 길주로 1", "TELNO": "032-000"},
            {"facNm": "나도서관", "ctprvnNm": "서울특별시", "signguNm": "구로구", "latitude": "37"}]}}}}
        items = ca.find_items(obj)
        self.assertEqual(len(items), 2)
        self.assertEqual(ca.region_of(items[0]), ("경기도", "부천시"))
        self.assertEqual(ca.region_of(items[1]), ("서울특별시", "구로구"))
        self.assertEqual(ca.total_count(obj), 2)

    def test_clean_drops_phone_coords(self):
        r = ca.clean_row({"facNm": "a", "TELNO": "1", "latitude": "2", "lon": "3", "rprsvNm": "홍", "addr": "x"})
        self.assertEqual(set(r), {"facNm", "addr"})

    def test_business_saves_counts_only_and_no_key(self):
        job = {"id": "license-x", "name": "x", "kind": "business", "key": "ZZ_KEY", "src": "s"}
        os.environ["ZZ_KEY"] = "SECRETKEY"
        pages = [json.dumps({"response": {"body": {"totalCount": 3, "items": {"item": [
            {"BPLC_NM": "업소1", "ROAD_NM_ADDR": "경기도 부천시 a"}, {"BPLC_NM": "업소2", "ROAD_NM_ADDR": "경기도 부천시 b"},
            {"BPLC_NM": "업소3", "LOTNO_ADDR": "서울특별시 구로구 c"}]}}}})]
        job["url"] = "https://example/info"
        res = ca.run_job(job, get=lambda u: pages[0], max_pages=2)
        with tempfile.TemporaryDirectory() as d:
            meta = ca.save(job, res, root=d, today="2026-11-26")
            self.assertEqual(meta["건수"], 3)
            self.assertFalse(os.path.exists(os.path.join(d, "license-x", "list.csv")))
            txt = open(os.path.join(d, "license-x", "by_sigungu.csv"), encoding="utf-8-sig").read()
            self.assertIn("부천시,2", txt)
            self.assertNotIn("SECRETKEY", json.dumps(meta, ensure_ascii=False))

    def test_error_redacts_key(self):
        os.environ["ZZ_KEY"] = "SECRETKEY"
        job = {"id": "x", "name": "x", "kind": "facility", "key": "ZZ_KEY", "src": "s", "url": "https://e/x"}
        res = ca.run_job(job, get=lambda u: "<html>bad SECRETKEY</html>", max_pages=1)
        self.assertNotIn("SECRETKEY", res["비고"])

    def test_xml_items(self):
        x = "<dbs><db><mt10id>1</mt10id><fcltynm>A</fcltynm><sidonm>경기</sidonm><gugunnm>부천시</gugunnm></db></dbs>"
        self.assertEqual(ca.xml_items(x)[0]["fcltynm"], "A")


if __name__ == "__main__":
    unittest.main()


class TPartial(unittest.TestCase):
    def test_partial_marked(self):
        os.environ["ZZ_KEY"] = "K"
        calls = {"n": 0}
        page = json.dumps({"items": [{"a": i, "ROAD_NM_ADDR": "경기도 부천시"} for i in range(2)]})

        def get(u):
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError("timed out")
            return page
        job = {"id": "x", "name": "x", "kind": "business", "key": "ZZ_KEY", "src": "s", "url": "https://e/x",
               "page": ("pageNo", "numOfRows", 2)}
        self.assertEqual(ca.run_job(job, get=get, max_pages=5)["상태"], "일부")
