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

URL_POST = rpc.Post(
    title="Poll post",
    subreddit="testing",
    scheduled_time=1000,
    data=rpc.Data(
        url=rpc.UrlPost(
            url="google.com",
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
        p = TEXT_POST
        self._conn.execute(
            QUERY_INSERT_POST, (p.SerializeToString(), p.scheduled_time, 0)
        )
        rows = get_all_rows(self._conn)
        self.assertGreater(len(rows), 0)
        post = make_post_from_row(rows[0])
        self.assertEqual(post.title, p.title)
        self.assertEqual(post.data.text.body, p.data.text.body)
        self.assertEqual(post.flair_id, p.flair_id)

    def test_db_add_post(self):
        db = Database("")
        db.adopt_connection_for_testing(self._conn)
        db.add_post(TEXT_POST)
        rows = get_all_rows(self._conn)
        self.assertGreater(len(rows), 0)
        e = rows[0]
        self.assertEqual(e["scheduled_time"], TEXT_POST.scheduled_time)
        self.assertEqual(e["error"], None)
        self.assertEqual(e["posted"], 0)

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
