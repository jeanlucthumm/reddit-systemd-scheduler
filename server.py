""" Defines the reddit-scheduler service.

Consists of 3 classes running on separate threads:
- Servicer: responds to client RPC calls
- Poster: periodically scans the database for posts ready to be posted
- Database: wrapper around the database

The Servicer and the Poster both enqueue commands in the Database.
"""
from concurrent import futures
from configparser import ConfigParser
import logging
import os
from queue import Queue
import queue
import sqlite3
import sys
import threading
import time
import time
from typing import Any, Callable

import grpc
import praw
from systemd import journal

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc

LOG_LEVEL = logging.DEBUG if os.environ.get("DEBUG") else logging.INFO
LOCK_TIMEOUT = 10  # seconds

# Logging setup
log = logging.getLogger()
log.addHandler(journal.JournalHandler())
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(LOG_LEVEL)
log.addHandler(stdout_handler)
log.setLevel(LOG_LEVEL)

CONFIG_SEARCH_PATHS = [
    os.environ.get("CONFIG_PATH"),
    os.path.expandvars("$HOME/.config/reddit-scheduler/config.ini"),
]
CONFIG_SEARCH_PATHS = [p for p in CONFIG_SEARCH_PATHS if p is not None]

ERR_MISSING_CONFIG = "Could not find a config file. Search path is: "
ERR_MISSING_CONFIG += ", ".join(CONFIG_SEARCH_PATHS)
ERR_INTERNAL = "internal error. See service logs"

# TODO how do you deal with schema updates?
# Existing table cols will not be updated due to IF NOT EXISTS
QUERY_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS Queue (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    subreddit TEXT NOT NULL,
    body TEXT,
    scheduled_time INTEGER NOT NULL,
    posted INTEGER NOT NULL
);
"""

QUERY_INSERT_POST = """
INSERT INTO Queue (title, subreddit, body, scheduled_time, posted)
VALUES (?, ?, ?, ?, ?);
"""

QUERY_ELIGIBLE = """
SELECT * FROM Queue
WHERE scheduled_time < strftime('%s','now')
AND posted == 0;
"""

QUERY_ALL = """
SELECT * FROM Queue;
"""

QUERY_DELETE = """
DELETE FROM Queue
WHERE id == ?;
"""

QUERY_MARK_POSTED = """
UPDATE Queue
SET posted = 1
WHERE id == ?;
"""

TEST_POST = rpc.Post(
    title="Hello there",
    subreddit="test",
    body="sample body disregard",
    scheduled_time=int(time.time()),
)


def validate_post(post: rpc.Post):
    # In proto3 unset values are equal to default values
    return post.title != "" and post.subreddit != "" and post.scheduled_time != 0


def make_post_from_row(row: sqlite3.Row):
    return rpc.Post(
        title=row["title"],
        subreddit=row["subreddit"],
        body=row["body"] if not None else "",
        scheduled_time=row["scheduled_time"],
    )


class DbCommand:
    """Primary way to instruct Database to do something.

    Command consist of a string descriptor and object payload and can be queued
    in the Database. The Database will reply via the `oneshot` channel
    """

    def __init__(self, command: str, obj: Any):
        self.command = command
        self.obj = obj
        self.oneshot = Queue(maxsize=1)

    # Datbase helpers
    def reply_ok(self, obj: Any):
        self.reply(obj, False)

    def reply_err(self, obj: Any):
        self.reply(obj, True)

    def reply(self, obj: Any, is_err: bool):
        self.oneshot.put_nowait(DbReply(obj, is_err))

    # Client helpers
    def wait_for_answer(self):
        return self.oneshot.get(timeout=LOCK_TIMEOUT)

    def __str__(self):
        return f"DbCommand ({self.command}, {self.obj})"


class DbReply:
    """Database sends this object as a reply in one shot channels of DbCommands."""

    def __init__(self, obj: Any, is_err: bool = False):
        self.is_err = is_err
        self.obj = obj

    def __str__(self):
        if self.is_err:
            return f"Err({self.obj})"
        else:
            return f"Ok({self.obj})"


class Database:
    """Wraps a SQL connection and provides an async channel for SQL operations."""

    def __init__(self, path: str):
        self.path = path
        self.queue = Queue(100)
        # We initialize the connection in start() so that all SQL components are
        # running in the same thread
        self.conn = None

    def queue_command(self, command: DbCommand):
        """Queue a command to be handled by the db later.

        This may block if the queue buffer if full and errors after LOCK_TIMEOUT.
        """
        log.debug("Database queued command: %s", command)
        try:
            self.queue.put(command, timeout=LOCK_TIMEOUT)
        except queue.Full:
            raise Exception("Service timeout: service may be overloaded")

    def start(self):
        # Initialize
        try:
            self.conn = sqlite3.connect(self.path)
            self.conn.row_factory = sqlite3.Row
        except Exception as e:
            raise Exception(f"Failed to initialize db at {self.path}") from e
        try:
            cur = self.conn.cursor()
            cur.execute(QUERY_CREATE_TABLE)
        except Exception as e:
            raise Exception("Failed to create database table") from e

        # Handle commands
        while True:
            entry: DbCommand = self.queue.get()
            log.debug("Database handling command: %s", entry)
            command = entry.command
            if command == "quit":
                log.debug("Stopping database")
                self.conn.close()
                break
            elif command == "post":
                try:
                    msg = self.add_post(entry.obj)
                    entry.reply(msg, msg != "")
                except:
                    log.exception("Failed to insert post into database:\n%s", entry.obj)
                    entry.reply_err(ERR_INTERNAL)
            elif command == "eligible":
                try:
                    posts = self.get_posts_from_query(QUERY_ELIGIBLE)
                    entry.reply(posts, posts == None)
                except:
                    log.exception("Failed to get eligible posts")
                    entry.reply_err(ERR_INTERNAL)
            elif command == "all":
                try:
                    all = self.get_posts_from_query(QUERY_ALL)
                    entry.reply(all, all == None)
                except:
                    log.exception("Failed to get all posts")
                    entry.reply_err(ERR_INTERNAL)
            elif command == "edit":
                try:
                    entry.reply_ok(self.edit_post(entry.obj))
                except:
                    log.exception("Failed to edit post")
                    entry.reply_err(ERR_INTERNAL)
            elif command == "mark_posted":
                try:
                    entry.reply_ok(self.mark_posted(entry.obj))
                except:
                    log.exception(
                        "Failed to mark post with id %d as posted", entry.obj.id
                    )
                    entry.reply_err(ERR_INTERNAL)

    def add_post(self, post: rpc.Post):
        if not validate_post(post):
            return "invalid post, client should not have sent this"
        self.conn.execute(
            QUERY_INSERT_POST,
            (post.title, post.subreddit, post.body, post.scheduled_time, 0),
        )
        self.conn.commit()

    def edit_post(self, request: rpc.EditPostRequest):
        if request.operation == rpc.EditPostRequest.Operation.DELETE:
            self.conn.execute(QUERY_DELETE, (request.id,))
            self.conn.commit()
        else:
            raise ValueError(f"unknown edit operation: {request.operation}")

    def mark_posted(self, post_id: int):
        self.conn.execute(QUERY_MARK_POSTED, (post_id,))
        self.conn.commit()

    def get_posts_from_query(self, query: str):
        posts = []
        for row in self.conn.execute(query):
            posts.append(
                rpc.PostDbEntry(
                    id=row["id"], posted=row["posted"], post=make_post_from_row(row)
                )
            )
        return posts


class Servicer(reddit_grpc.RedditSchedulerServicer):
    """Implementation of grpc service which responds to client requests."""

    def ListPosts(self, request, _):
        return self.database_op(
            DbCommand("all", None),
            "ListPosts",
            request,
            lambda msg, obj: rpc.ListPostsReply(error_msg=msg, posts=obj),
        )

    def SchedulePost(self, request, _):
        return self.database_op(
            DbCommand("post", request),
            "SchedulePost",
            request,
            lambda msg, _: rpc.SchedulePostReply(error_msg=msg),
        )

    def EditPost(self, request, _):
        return self.database_op(
            DbCommand("edit", request),
            "EditPost",
            request,
            lambda msg, _: rpc.EditPostReply(error_msg=msg),
        )

    def database_op(
        self,
        command: DbCommand,
        rpc_name: str,
        request: Any,
        reply_handler: Callable[[str, Any], Any],
    ):
        log.debug("Got %s RPC", rpc_name)
        try:
            self.db.queue_command(command)
            reply = command.oneshot.get(timeout=LOCK_TIMEOUT)
            msg = str(reply.obj) if reply.is_err else ""
            return reply_handler(msg, reply.obj)
        except queue.Empty:
            log.exception(
                "%s RPC timed out waiting for database with command:\n%s",
                rpc_name,
                command,
            )
            return reply_handler(ERR_INTERNAL, None)
        except:
            log.exception("Error handling %s RPC with request:\n%s", request)
            return reply_handler(ERR_INTERNAL, None)

    def link_database(self, db):
        self.db = db
        return self


def post_to_reddit(reddit: praw.Reddit, entry: rpc.PostDbEntry):
    log.info("Posting post with id %d to reddit", entry.id)
    post = entry.post
    subreddit = reddit.subreddit(post.subreddit)
    subreddit.submit(title=post.title, selftext=post.body, url=None)
    log.info("Submitted post with id %d", entry.id)


def simulate_post(post):
    log.debug("Would've posted: %s", post)


class Poster:
    """Routinely checks if any posts are eligible to be posted and then posts them to Reddit."""

    def __init__(self, reddit_config, dry_run: bool = True, step_interval: float = 5):
        self.dry_run = dry_run
        self.step_interval = step_interval

        cfg = reddit_config
        self.reddit = praw.Reddit(
            client_id=cfg["ClientId"],
            client_secret=cfg["ClientSecret"],
            password=cfg["Password"],
            username=cfg["Username"],
            user_agent=f"desktop:{cfg['ClientId']}:v0.0.1  (by u/{cfg['Username']})",
        )

    def step(self):
        """Posts all eligible posts and marks them as posted in the datbase."""
        log.debug("Poster doing step")
        # Get the eligible posts from the database
        eligible = []
        try:
            command = DbCommand("eligible", None)
            self.db.queue_command(command)
            db_reply = command.wait_for_answer()
            if db_reply.is_err:
                raise ValueError(db_reply.obj)
            eligible = db_reply.obj
        except:
            log.exception("Poster step errored on db command")
        log.debug("Got %d eligible posts", len(eligible))

        # Post everything to reddit
        posted = []
        for entry in eligible:
            if self.dry_run:
                simulate_post(entry.post)
                posted.append(entry)
            else:
                try:
                    post_to_reddit(self.reddit, entry)
                    posted.append(entry)
                except:
                    log.exception("Failed to post post with id %d", entry.id)

        # Tell database which posts we posted
        for entry in posted:
            try:
                command = DbCommand("mark_posted", entry.id)
                self.db.queue_command(command)
                db_reply = command.wait_for_answer()
                if db_reply.is_err:
                    raise ValueError(db_reply.obj)
            except:
                log.exception("Poster step errored on telling db about posted")

    def start(self):
        # TODO figure out how to stop this
        while True:
            self.step()
            time.sleep(self.step_interval)

    def link_database(self, db):
        self.db = db
        return self


def database_thread(db: Database):
    log.debug("Starting database with path %s", db.path)
    db.start()


def poster_thread(poster: Poster):
    log.debug("Starting poster")
    poster.start()


def get_config():
    for p in CONFIG_SEARCH_PATHS:
        if os.path.exists(p):
            parser = ConfigParser()
            parser.read(p)
            return parser
    log.error(ERR_MISSING_CONFIG)
    return None


def is_valid_config(config: ConfigParser):
    try:
        general = config["General"]
        general.getint("Port")
        general.getfloat("PostInterval")
        general.getboolean("DryRun")

        reddit = config["RedditAPI"]
        reddit["Username"]
        reddit["Password"]
        reddit["ClientId"]
        reddit["ClientSecret"]
        return True
    except ValueError as e:
        log.error("Config files contains errors: %s", e)
    except KeyError as e:
        log.error("Config file missing section or value: %s", e)
    return False


if __name__ == "__main__":
    config = get_config()
    if config is None or not is_valid_config(config):
        sys.exit(1)
    general = config["General"]

    # Start database
    db = Database(os.environ["DBPATH"])
    threading.Thread(target=database_thread, args=(db,)).start()

    # Start poster
    poster = Poster(
        config["RedditAPI"],
        bool(os.environ.get("DRY_RUN")) or general.getboolean("DryRun"),
        general.getint("PostInterval"),
    )
    poster.link_database(db)
    threading.Thread(target=poster_thread, args=(poster,)).start()

    # Start RPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    reddit_grpc.add_RedditSchedulerServicer_to_server(
        Servicer().link_database(db), server
    )
    addr = f"[::]:{general.getint('Port')}"
    server.add_insecure_port(addr)
    log.debug("Starting rpc server on %s", addr)
    log.info("Service started on %s", addr)

    server.start()
    server.wait_for_termination()
    db.queue_command(DbCommand(command="quit", obj=None))
