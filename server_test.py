import unittest
import sqlite3

from server import *


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._conn = sqlite3.connect(":memory:")
        cls._conn.row_factory = sqlite3.Row
        cls._conn.execute(QUERY_CREATE_TABLE)

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()

    def tearDown(self):
        self._conn.execute("DELETE FROM Queue")

    def test_make_post_from_row(self):
        self._conn.execute(QUERY_INSERT_TEXT_POST, ("Title", "test", "body", 1647785133, 0))
        rows = []
        for row in self._conn.execute(QUERY_ALL):
            rows.append(row)
        self.assertGreater(len(rows), 0)
        post = make_post_from_row(rows[0])
        self.assertEqual(post.text_post.title, "Title")
        self.assertEqual(post.text_post.body, "body")


if __name__ == "__main__":
    unittest.main()
