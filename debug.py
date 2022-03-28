import sqlite3
import os
import sys
import reddit_pb2 as rpc


def dump():
    path = os.path.expandvars("$HOME/.config/reddit-scheduler/database.sqlite")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for row in conn.execute("SELECT * FROM Queue"):
        post = rpc.Post(
            title=row["title"],
            subreddit=row["subreddit"],
            scheduled_time=row["scheduled_time"],
        )
        post.data.ParseFromString(row["data"])
        print(post)


FUNC_MAP = {"dump": dump}

if __name__ == "__main__":
    FUNC_MAP[sys.argv[1]]()
