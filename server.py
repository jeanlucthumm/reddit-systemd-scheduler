from concurrent import futures
import configparser
import logging
import time
import os
import sqlite3
import threading
from queue import Queue
import queue
import time
import sys
from configparser import ConfigParser

import grpc
import praw
from systemd import journal

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc

LOG_LEVEL = logging.DEBUG
LOCK_TIMEOUT = 2  # seconds

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


def validate_post(post):
    # In proto3 unset values are equal to default values
    return post.title != "" and post.subreddit != "" and post.scheduled_time != 0


def make_post_from_row(row):
    return rpc.Post(
        title=row["title"],
        subreddit=row["subreddit"],
        body=row["body"] if not None else "",
        scheduled_time=row["scheduled_time"],
    )


class DbCommand:
    def __init__(self, command, obj):
        self.command = command
        self.obj = obj
        self.oneshot = Queue(maxsize=1)

    def reply_err(self, err):
        self.oneshot.put_nowait(DbReply(err, True))

    def reply_ok(self, obj=None):
        self.oneshot.put_nowait(DbReply(obj, False))

    def wait_for_answer(self):
        return self.oneshot.get(timeout=LOCK_TIMEOUT)

    def __str__(self):
        return f"DbCommand ({self.command}, {self.obj})"


class DbReply:
    def __init__(self, obj, is_err=False):
        self.is_err = is_err
        self.obj = obj

    def __str__(self):
        if self.is_err:
            return f"Err({self.obj})"
        else:
            return f"Ok({self.obj})"


class Database:
    def __init__(self, path):
        self.path = path
        self.queue = Queue(100)
        # We initialize the connection in start() so that all SQL components are
        # running in the same thread
        self.conn = None

    def queue_command(self, command):
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
            entry = self.queue.get()
            log.debug("Database handling command: %s", entry)
            command = entry.command
            if command == "quit":
                log.debug("Stopping database")
                self.conn.close()
                break
            elif command == "post":
                try:
                    result = self.add_post(entry.obj)
                    entry.reply_ok(result)
                except:
                    log.exception("Failed to insert post into database:\n%s", entry.obj)
                    entry.reply_err("internal error. See service logs")
            elif command == "eligible":
                try:
                    result = self.get_posts_from_query(QUERY_ELIGIBLE)
                    entry.reply_ok(result)
                except:
                    log.exception("Failed to get eligible posts")
                    entry.reply_err("internal error. See service logs")
            elif command == "all":
                try:
                    result = self.get_posts_from_query(QUERY_ALL)
                    entry.reply_ok(result)
                except:
                    log.exception("Failed to get all posts")
                    entry.reply_err("internal error. See service logs")
            elif command == "edit":
                try:
                    result = self.edit_post(entry.obj)
                    entry.reply_ok(result)
                except:
                    log.exception("Failed to get all posts")
                    entry.reply_err("internal error. See service logs")
            elif command == "mark_posted":
                try:
                    result = self.mark_posted(entry.obj)
                    entry.reply_ok(result)
                except:
                    log.exception("Failed to get all posts")
                    entry.reply_err("internal error. See service logs")

    def add_post(self, post):
        if not validate_post(post):
            # TODO these should be string replies because it's an RPC usage error
            raise ValueError("invalid post")
        self.conn.execute(
            QUERY_INSERT_POST,
            (post.title, post.subreddit, post.body, post.scheduled_time, 0),
        )
        self.conn.commit()

    def edit_post(self, request):
        if request.operation == rpc.EditPostRequest.Operation.DELETE:
            self.conn.execute(QUERY_DELETE, (request.id,))
            self.conn.commit()
        else:
            raise ValueError(f"unknown edit operation: {request.operation}")

    def mark_posted(self, post_id):
        self.conn.execute(QUERY_MARK_POSTED, (post_id,))
        self.conn.commit()

    def get_posts_from_query(self, query):
        posts = []
        for row in self.conn.execute(query):
            posts.append(
                rpc.PostDbEntry(
                    id=row["id"], posted=row["posted"], post=make_post_from_row(row)
                )
            )
        return posts


class Servicer(reddit_grpc.RedditSchedulerServicer):
    def ListPosts(self, request, context):
        log.debug("Got ListPosts RPC")
        try:
            command = DbCommand("all", None)
            self.db.queue_command(command)
            db_reply = command.oneshot.get(timeout=LOCK_TIMEOUT)
            msg = db_reply.obj if db_reply.is_err else ""
            return rpc.ListPostsReply(posts=db_reply.obj, error_msg=msg)
        except queue.Empty:
            log.exception(
                "ListPosts RPC timed out waiting for database with request:\n%s",
                request,
            )
        except:
            log.exception("Error handling SchedulePost RPC with request:\n%s", request)
            return rpc.ListPostsReply(error_msg="internal server error. check logs")

    def SchedulePost(self, request, context):
        log.debug("Got SchedulePost RPC")
        try:
            command = DbCommand("post", request)
            self.db.queue_command(command)
            db_reply = command.oneshot.get(timeout=LOCK_TIMEOUT)
            msg = db_reply.obj if db_reply.is_err else ""
            return rpc.SchedulePostReply(error_msg=msg)
        except queue.Empty:
            log.exception(
                "SchedulePost RPC timed out waiting for database with request:\n%s",
                request,
            )
        except:
            log.exception("Error handling SchedulePost RPC with request:\n%s", request)
            return rpc.SchedulePostReply(error_msg="internal server error. check logs")

    def EditPost(self, request, context):
        # TODO generalize this
        log.debug("Got EditPost RPC")
        try:
            command = DbCommand("edit", request)
            self.db.queue_command(command)
            db_reply = command.oneshot.get(timeout=LOCK_TIMEOUT)
            msg = db_reply.obj if db_reply.is_err else ""
            return rpc.SchedulePostReply(error_msg=msg)
        except queue.Empty:
            log.exception(
                "EditPost RPC timed out waiting for database with request:\n%s",
                request,
            )
        except:
            log.exception("Error handling SchedulePost RPC with request:\n%s", request)
            return rpc.SchedulePostReply(error_msg="internal server error. check logs")

    def link_poster(self, poster):
        self.poster = poster
        return self

    def link_database(self, db):
        self.db = db
        return self


def post_to_reddit(reddit, post):
    print("Posting to subreddit")
    subreddit = reddit.subreddit(post.subreddit)
    subreddit.submit(title=post.title, selftext=post.body, url=None)
    print("Submitted")


def simulate_post(post):
    log.debug("Would've posted: %s", post)


class Poster:
    def __init__(self, reddit_config, dry_run=True, step_interval=5):
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
                    post_to_reddit(self.reddit, entry.post)
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
        general.getint("PostInterval")
        general.getboolean("DryRun")
        return True
    except ValueError as e:
        log.error("Config files contains errors: %s", e)
    except KeyError as e:
        log.error("Config file missing section: %s", e)
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
        os.environ.get("DRY_RUN") or general.getboolean("DryRun"),
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
    log.info("Starting rpc server on %s", addr)

    server.start()
    server.wait_for_termination()
    db.queue_command(DbCommand(command="quit", obj=None))
