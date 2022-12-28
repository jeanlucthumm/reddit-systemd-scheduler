import unittest
import sqlite3
import reddit_pb2 as rpc

from server import *
from typing import List

TEXT_POST = rpc.Post(
    title="Hello there",
    subreddit="test",
    scheduled_time=1000,
    data=rpc.Data(
        text=rpc.TextPost(
            body="sample body",
        )
    ),
)

POLL_POST = rpc.Post(
    title="Poll post",
    subreddit="testing",
    scheduled_time=1000,
    data=rpc.Data(
        poll=rpc.PollPost(
            selftext="selftext",
            duration=2,
            options=["option 1", "option 2"],
        )
    ),
    flair_id="ID_FOR_FLAIR",
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
        data = rpc.Data(text=rpc.TextPost(body="body"))
        self._conn.execute(
            QUERY_INSERT_POST,
            ("text", "Title", "test", data.SerializeToString(), 1647785133, 0, None),
        )
        rows = get_all_rows(self._conn)
        self.assertGreater(len(rows), 0)
        post = make_post_from_row(rows[0])
        self.assertEqual(post.title, "Title")
        self.assertEqual(post.data.text.body, "body")
        self.assertEqual(post.flair_id, "")

    def test_db_add_text_post(self):
        db = Database("")
        db.adopt_connection_for_testing(self._conn)
        db.add_post(TEXT_POST)
        rows = get_all_rows(self._conn)
        self.assertGreater(len(rows), 0)
        e = rows[0]
        self.assertEqual(e["type"], "text")
        self.assertEqual(e["title"], "Hello there")
        data = rpc.Data()
        data.ParseFromString(e["data"])
        self.assertEqual(data.text.body, "sample body")

    def test_db_add_poll_post(self):
        db = Database("")
        db.adopt_connection_for_testing(self._conn)
        db.add_post(POLL_POST)
        rows = get_all_rows(self._conn)
        self.assertGreater(len(rows), 0)
        e = rows[0]
        self.assertEqual(e["type"], "poll")
        self.assertEqual(e["title"], "Poll post")
        self.assertEqual(e["flair_id"], "ID_FOR_FLAIR")
        data = rpc.Data()
        data.ParseFromString(e["data"])
        self.assertEqual(data.poll.selftext, "selftext")

    def test_db_mark_error(self):
        db = Database("")
        db.adopt_connection_for_testing(self._conn)

        db.queue_command(DbCommand("post", POLL_POST))
        db.step()

        e = get_all_rows(self._conn)[0]
        err = "random error\nwith newline"
        db.queue_command(DbCommand("mark_error", ObjMarkError(e["id"], err)))
        db.step()

        e = get_all_rows(self._conn)[0]
        self.assertEqual(e["error"], err)


if __name__ == "__main__":
    unittest.main()
