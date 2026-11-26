import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collectors"))
import ordinance_lib as o

TREES = {
    "main": [("경기도", "tree", "r1"), (".github", "tree", "x")],
    "r1": [("부천시", "tree", "b1"), ("README.md", "blob", "y")],
    "b1": [("조례", "tree", "k1"), ("훈령", "tree", "k2")],
    "k1": [("부천시립도서관 운영 조례", "tree", "l1"), ("부천시 정책자문 조례", "tree", "l2"),
           ("부천시 독서문화진흥 조례", "tree", "l3")],
    "k2": [("부천시 도서관 복무 훈령", "tree", "l4")],
}
BODY = "---\n자치법규ID: '123'\n자치법규일련번호: '456'\n자치법규명: 부천시립도서관 운영 조례\n공포일자: '2024-01-02'\n---\n제1조 ...\n"

def fake_get(url, accept=None):
    if "/git/trees/" in url:
        key = url.rsplit("/", 1)[1]
        return json.dumps({"tree": [{"path": p, "type": t, "sha": s} for p, t, s in TREES[key]]})
    return BODY

class T(unittest.TestCase):
    def test_keywords(self):
        self.assertTrue(o.is_library("작은도서관 진흥 조례"))
        self.assertFalse(o.is_library("정책자문위원회 조례"))

    def test_collect(self):
        with tempfile.TemporaryDirectory() as d:
            rows, meta = o.collect(d, get=fake_get, today="2026-11-26", log=lambda *a: None)
            names = [r["경로"].split("/")[-1] for r in rows]
            self.assertEqual(sorted(names), ["부천시 독서문화진흥 조례", "부천시립도서관 운영 조례"])
            self.assertEqual(meta["건수"], 2)
            self.assertIn("ordinSeq=456", rows[0]["원천주소"])
            self.assertTrue(os.path.exists(os.path.join(d, "index.csv")))
            self.assertTrue(os.path.exists(os.path.join(d, "text", "경기도", "부천시", "조례", "부천시립도서관 운영 조례.md")))

    def test_frontmatter(self):
        fm = o.parse_frontmatter(BODY)
        self.assertEqual(fm["공포일자"], "2024-01-02")

if __name__ == "__main__":
    unittest.main()


class TestLawCheck(unittest.TestCase):
    def test_compare(self):
        import law_check as lc
        data = {"OrdinSearch": {"totalCnt": 3, "law": [
            {"자치법규ID": "1", "자치법규명": "가시 도서관 조례", "공포일자": "20261101"},
            {"자치법규ID": "2", "자치법규명": "나시 독서문화진흥 조례", "공포일자": "20261120"},
            {"자치법규ID": "3", "자치법규명": "다시 정책자문 조례", "공포일자": "20261120"}]}}
        items = lc.items_of(data)
        new, total = lc.compare(items, {"1"})
        self.assertEqual(total, 2)
        self.assertEqual([n["자치법규ID"] for n in new], ["2"])

    def test_key_not_leaked(self):
        import law_check as lc
        with self.assertRaises(RuntimeError) as cm:
            lc.search("SECRETOC", "도서관", get=lambda u: "<html>SECRETOC 차단</html>")
        self.assertNotIn("SECRETOC", str(cm.exception))
