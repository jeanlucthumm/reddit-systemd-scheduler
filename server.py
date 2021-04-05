from concurrent import futures
from threading import Lock
import configparser
import logging
import time
import sqlite3
import os

import grpc
import praw
from systemd import journal

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc

# <platform>:<app ID>:<version string> (by u/<Reddit username>)

# Set up logging with systemd
log = logging.getLogger()
log.addHandler(journal.JournalHandler())

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
        print("Got list posts RPC")
        posts = ["Hello", "There", "How"]
        return rpc.ListPostsReply(posts=posts)

    def SchedulePost(self, request, context):
        self.poster.schedule_post(request)

    def link_poster(self, poster):
        self.poster = poster
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


def serve(poster):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    reddit_grpc.add_RedditSchedulerServicer_to_server(
        Servicer().link_poster(poster), server
    )
    server.add_insecure_port("[::]:50051")
    print("Starting server...")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    db = Database(os.environ["DBPATH"])
    db.add_post(TEST_POST)
