import unittest
import sqlite3
import reddit_pb2 as rpc

from server import *
from typing import List

TEXT_POST = rpc.TextPost(
    title="Hello there",
    subreddit="test",
    body="sample body disregard",
    scheduled_time=int(time.time()),
)


def get_all_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    rows = []
    for row in conn.execute(QUERY_ALL):
        rows.append(row)
    return rows


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
        # Text post
        self._conn.execute(
            QUERY_INSERT_TEXT_POST, ("Title", "test", "body", 1647785133, 0)
        )
        rows = get_all_rows(self._conn)
        self.assertGreater(len(rows), 0)
        post = make_post_from_row(rows[0])
        self.assertEqual(post.text_post.title, "Title")
        self.assertEqual(post.text_post.body, "body")

    def test_db_add_post(self):
        # Text post
        db = Database("")
        db.adopt_connection_for_testing(self._conn)
        post = rpc.Post(text_post=TEXT_POST)
        db.add_post(post)
        rows = get_all_rows(self._conn)
        self.assertGreater(len(rows), 0)
        e = rows[0]
        self.assertEqual(e["type"], "text")
        self.assertEqual(e["title"], "Hello there")


if __name__ == "__main__":
    unittest.main()
