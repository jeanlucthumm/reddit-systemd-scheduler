from concurrent import futures
import configparser
import logging
import os
import sqlite3
from threading import Lock
import time
import sys

import grpc
import praw
from systemd import journal

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc

# Logging setup
LOG_LEVEL = logging.DEBUG
log = logging.getLogger()
log.addHandler(journal.JournalHandler())
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(LOG_LEVEL)
log.addHandler(stdout_handler)
log.setLevel(LOG_LEVEL)

QUERY_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS Queue (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    subreddit TEXT NOT NULL,
    body TEXT,
    scheduled_time INTEGER NOT NULL
);
"""

QUERY_INSERT_POST = """
INSERT INTO Queue (title, subreddit, body, scheduled_time)
VALUES (?, ?, ?, ?);
"""

TEST_POST = rpc.Post(
    title="Hello there",
    subreddit="test",
    body="sample body disregard",
    scheduled_time=int(time.time()),
)


def validate_post(post):
    # In proto3 unset values are equal to default values
    return post.title != "" and post.subreddit != "" and post.scheduled_time != 0


class Database:
    def __init__(self, db_path):
        try:
            self.conn = sqlite3.connect(db_path)
        except Exception as e:
            raise Exception(f"Failed to initialize db at {db_path}") from e
        try:
            cur = self.conn.cursor()
            cur.execute(QUERY_CREATE_TABLE)
        except Exception as e:
            raise Exception("Failed to create database table") from e
        self.lock = Lock()

    def add_post(self, post):
        try:
            self.lock.acquire()
            if not validate_post(post):
                return "invalid post"

            cur = self.conn.cursor()
            cur.execute(
                QUERY_INSERT_POST,
                (post.title, post.subreddit, post.body, post.scheduled_time),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            raise Exception(f"Failed to insert post into database:\n{post}") from e
        finally:
            self.lock.release()


def post_to_reddit(reddit, post):
    print("Posting to subreddit")
    subreddit = reddit.subreddit(post.subreddit)
    subreddit.submit(title=post.title, selftext=post.body, url=None)
    print("Submitted")


class Servicer(reddit_grpc.RedditSchedulerServicer):
    def ListPosts(self, request, context):
        log.debug("Got ListPosts RPC")
        posts = ["Hello", "There", "How"]
        return rpc.ListPostsReply(posts=posts)

    def SchedulePost(self, request, context):
        log.debug("Got SchedulePost RPC")
        try:
            msg = self.db.add_post(request)
            msg = msg if not None else ""
            return rpc.SchedulePostReply(error_msg=msg)
        except Exception as e:
            log.exception("Error handling SchedulePost RPC with request:\n%s", request)
            return rpc.SchedulePostReply(error_msg="internal server error. check logs")

    def link_poster(self, poster):
        self.poster = poster
        return self

    def link_database(self, db):
        self.db = db
        return self


class Poster:
    def __init__(self, config_path):
        self.config = configparser.ConfigParser()
        self.config.read(config_path)
        self.queue = []

        cfg = self.config["RedditAPI"]
        self.reddit = praw.Reddit(
            client_id=cfg["ClientId"],
            client_secret=cfg["ClientSecret"],
            password=cfg["Password"],
            username=cfg["Username"],
            user_agent=f"desktop:{cfg['ClientId']}:v0.0.1  (by u/{cfg['Username']})",
        )

    def schedule_post(self, post):
        self.queue.append(post)

    def start(self):
        post_to_reddit(self.reddit, TEST_POST)


if __name__ == "__main__":
    db = Database(os.environ["DBPATH"])
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    reddit_grpc.add_RedditSchedulerServicer_to_server(
        Servicer().link_database(db), server
    )
    addr = "[::]:50051"
    server.add_insecure_port(addr)
    log.info("Starting server on %s", addr)
    server.start()
    server.wait_for_termination()
