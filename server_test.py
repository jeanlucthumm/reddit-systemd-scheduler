import unittest
import sqlite3
import reddit_pb2 as rpc

from server import *
from typing import List

TEXT_POST = rpc.Post(
    title="Hello there",
    subreddit="test",
    scheduled_time=int(time.time()),
    data=rpc.Data(
        text=rpc.TextPost(
            body="sample body disregard",
        )
    ),
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
        self.assertEqual(post.title, "Title")
        self.assertEqual(post.data.text.body, "body")

    def test_db_add_post(self):
        # Text post
        db = Database("")
        db.adopt_connection_for_testing(self._conn)
        db.add_post(TEXT_POST)
        rows = get_all_rows(self._conn)
        self.assertGreater(len(rows), 0)
        e = rows[0]
        self.assertEqual(e["type"], "text")
        self.assertEqual(e["title"], "Hello there")


if __name__ == "__main__":
    unittest.main()
