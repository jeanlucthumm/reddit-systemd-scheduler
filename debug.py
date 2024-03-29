from configparser import ConfigParser
import sqlite3
import os
import sys
import reddit_pb2 as rpc
import praw


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

def reddit_instance():
    parser = ConfigParser()
    parser.read(os.path.expandvars("$HOME/.config/reddit-scheduler/config.ini"))

    cfg = parser["RedditAPI"]
    return praw.Reddit(
        client_id=cfg["ClientId"],
        client_secret=cfg["ClientSecret"],
        password=cfg["Password"],
        username=cfg["Username"],
        user_agent=f"desktop:{cfg['ClientId']}:v0.0.1  (by u/{cfg['Username']})",
    )


FUNC_MAP = {"dump": dump}

if __name__ == "__main__":
    FUNC_MAP[sys.argv[1]]()
