""" Defines the reddit-scheduler service.

Consists of 3 classes running on separate threads:
- Servicer: responds to client RPC calls
- Poster: periodically scans the database for posts ready to be posted
- Database: wrapper around the database

The Servicer and the Poster both enqueue commands in the Database.

Environment variables:
DEBUG:          Enables log granularity
LOG_STDOUT:     Enables logging to stdout
CONFIG_PATH:    Set path of config file. Otherwise searches as defined in the global
                var CONFIG_SEARCH_PATHS
DB_PATH:        Sets the path to the database to use. Creates new database if none is found there
"""
from concurrent import futures
from configparser import ConfigParser
import logging
from praw.exceptions import RedditAPIException
import os
from queue import Queue
import queue
import uuid
import sqlite3
import sys
from pathlib import Path
import threading
import time
import time
from typing import Any, Callable, Optional, List, cast

import grpc
import praw
from systemd import journal, daemon

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc

LOG_LEVEL = logging.DEBUG if os.environ.get("DEBUG") else logging.INFO
LOCK_TIMEOUT = 10  # seconds

# Logging setup
log = logging.getLogger()
log.addHandler(journal.JournalHandler())
stdout_handler = logging.StreamHandler(sys.stdout)
if os.environ.get("LOG_STDOUT"):
    log.addHandler(stdout_handler)


def set_debug_level(level):
    stdout_handler.setLevel(level)
    log.setLevel(level)


CONFIG_SEARCH_PATHS = [
    os.environ.get("CONFIG_PATH"),
    os.path.expandvars("$HOME/.config/reddit-scheduler/config.ini"),
]
CONFIG_SEARCH_PATHS = [p for p in CONFIG_SEARCH_PATHS if p is not None]

ERR_MISSING_CONFIG = "Could not find a config file. Search path is: "
ERR_MISSING_CONFIG += ", ".join(CONFIG_SEARCH_PATHS)
ERR_INTERNAL = (
    "internal error. See service logs via `systemctl --user status reddit-scheduler`"
)

# TODO how do you deal with schema updates? ==> separate table with version
# Existing table cols will not be updated due to IF NOT EXISTS
QUERY_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS Queue (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    subreddit TEXT NOT NULL,
    data BLOB NOT NULL,
    scheduled_time INTEGER NOT NULL,
    posted INTEGER NOT NULL,
    flair_id TEXT,
    error TEXT
);
"""

QUERY_INSERT_POST = """
INSERT INTO Queue (type, title, subreddit, data, scheduled_time, posted, flair_id)
VALUES (?, ?, ?, ?, ?, ?, ?);
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

QUERY_MARK_ERROR = """
UPDATE Queue
SET error = ?
WHERE id == ?;
"""


# TODO validate data field as well (or delegate to praw)
def validate_post(post: rpc.Post):
    # In proto3 unset values are equal to default values
    return post.title != "" and post.subreddit != "" and post.scheduled_time != 0


def make_post_from_row(row: sqlite3.Row) -> rpc.Post:
    post = rpc.Post(
        title=row["title"],
        subreddit=row["subreddit"],
        scheduled_time=row["scheduled_time"],
        flair_id=row["flair_id"] or "",
    )
    post.data.ParseFromString(row["data"])
    return post


class DbCommand:
    """Primary way to instruct Database to do something.

    Command consist of a string descriptor and object payload and can be queued
    in the Database. The Database will reply via the `oneshot` channel
    """

    def __init__(self, command: str, obj: Any):
        self.command = command
        self.obj = obj
        self.oneshot = Queue(maxsize=1)  # type: Queue[DbReply]

    # Database helpers
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


class ObjMarkError:
    """Obj included in a mark_error DbCommand."""

    def __init__(self, id: int, err: str) -> None:
        self.id = id
        self.err = err


class Database:
    """Wraps a SQL connection and provides an async channel for SQL operations."""

    def __init__(self, path: str):
        self.path = path
        self.queue = Queue(100)
        # We initialize the connection in start() so that all SQL components are
        # running in the same thread
        self.conn: Optional[sqlite3.Connection] = None

    def adopt_connection_for_testing(self, conn: sqlite3.Connection):
        self.conn = conn

    def queue_command(self, command: DbCommand):
        """Queue a command to be handled by the db later.

        This may block if the queue buffer is full and errors after LOCK_TIMEOUT.
        """
        log.debug("Database queued command: %s", command)
        try:
            self.queue.put(command, timeout=LOCK_TIMEOUT)
        except queue.Full:
            raise Exception("Service timeout: service may be overloaded")

    def start(self):
        self.initialize()
        self.handle_commands()

    def initialize(self):
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

    def step(self) -> bool:
        if self.conn == None:
            assert False
        entry: DbCommand = self.queue.get()
        log.debug("Database handling command: %s", entry)
        command = entry.command
        if command == "quit":
            log.debug("Stopping database")
            self.conn.close()
            return False
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
                log.exception("Failed to mark post with id %d as posted", entry.obj)
                entry.reply_err(ERR_INTERNAL)
        elif command == "mark_error":
            obj = cast(ObjMarkError, entry.obj)
            try:
                entry.reply_ok(self.mark_error(obj.id, obj.err))
            except:
                log.exception(
                    "Failed to mark post with id %d as error %s",
                    obj.id,
                    obj.err,
                )
                entry.reply_err(ERR_INTERNAL)
        return True

    def handle_commands(self):
        while self.step():
            pass

    def add_post(self, p: rpc.Post) -> str:
        if self.conn == None:
            assert False

        data_type = ""
        if p.data.HasField("text"):
            data_type = "text"
        elif p.data.HasField("poll"):
            data_type = "poll"
        elif p.data.HasField("image"):
            data_type = "image"
            if len(p.data.image.image_data) == 0:
                return "cannot post empty image post"
        elif p.data.HasField("url"):
            data_type = "url"
        else:
            raise ValueError(f"could not determine type of post to add: {p}")

        if not validate_post(p):
            return "invalid post, client should not have sent this"
        self.conn.execute(
            QUERY_INSERT_POST,
            (
                data_type,
                p.title,
                p.subreddit,
                p.data.SerializeToString(),
                p.scheduled_time,
                0,
                None if p.flair_id == "" else p.flair_id,
            ),
        )
        self.conn.commit()
        return ""

    def edit_post(self, request: rpc.EditPostRequest):
        if self.conn == None:
            assert False
        if request.operation == rpc.EditPostRequest.Operation.DELETE:
            self.conn.execute(QUERY_DELETE, (request.id,))
            self.conn.commit()
        else:
            raise ValueError(f"unknown edit operation: {request.operation}")

    def mark_posted(self, post_id: int):
        if self.conn == None:
            assert False
        self.conn.execute(QUERY_MARK_POSTED, (post_id,))
        self.conn.commit()

    def mark_error(self, post_id: int, err: str):
        if self.conn == None:
            assert False
        self.conn.execute(QUERY_MARK_ERROR, (err, post_id))
        self.conn.commit()

    def get_posts_from_query(self, query: str):
        if self.conn == None:
            assert False
        posts = []
        for row in self.conn.execute(query):
            status = rpc.PostStatus.UNKNOWN
            error = ""
            if row["error"] is not None:
                status = rpc.PostStatus.ERROR
                error = row["error"]
            elif row["posted"]:
                status = rpc.PostStatus.POSTED
            else:
                status = rpc.PostStatus.PENDING
            posts.append(
                rpc.PostDbEntry(
                    id=row["id"],
                    post=make_post_from_row(row),
                    status=status,
                    error=error,
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

    def ListFlairs(self, request, _):
        flairs = []
        try:
            reddit = get_reddit(self.reddit_config)
            return rpc.ListFlairsResponse(
                flairs=flairs_for_subdreddit(reddit, request.subreddit)
            )
        except Exception as e:
            log.error(
                f"Recovering from ListFlairs error for subreddit {request.subreddit}:\n{str(e)}"
            )
        return rpc.ListFlairsResponse(flairs=flairs)

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
            log.exception("Error handling %s RPC with request:\n%s", rpc_name, request)
            return reply_handler(ERR_INTERNAL, None)

    def link_database(self, db):
        self.db = db
        return self

    def set_reddit_config(self, reddit_config):
        self.reddit_config = reddit_config
        return self


def post_to_reddit(reddit: praw.Reddit, entry: rpc.PostDbEntry):
    log.info("Posting post with id %d to reddit", entry.id)
    p = entry.post
    subreddit = reddit.subreddit(p.subreddit)
    flair_id = p.flair_id if p.flair_id != "" else None
    if p.data.HasField("text"):
        subreddit.submit(title=p.title, selftext=p.data.text.body, flair_id=flair_id)
        log.info("Submitted post with id %d", entry.id)
    elif p.data.HasField("poll"):
        poll = p.data.poll
        kwargs = {}
        if poll.duration != 0:
            kwargs["duration"] = poll.duration
        subreddit.submit_poll(
            title=p.title,
            options=list(poll.options),
            selftext=poll.selftext,
            flair_id=flair_id,
            **kwargs,
        )
    elif p.data.HasField("image"):
        image = p.data.image
        path = (Path("/tmp/reddit-scheduler") / str(uuid.uuid1())).with_suffix(
            "." + image.extension
        )
        log.debug("Writing temporary image to %s", path)
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "wb") as f:
            f.write(image.image_data)
        subreddit.submit_image(
            title=p.title, flair_id=flair_id, nsfw=image.nsfw, image_path=str(path)
        )
    elif p.data.HasField("url"):
        subreddit.submit(title=p.title, url=p.data.url.url, flair_id=flair_id)
        log.info("Submitted post with id %d", entry.id)
    else:
        raise ValueError(f"could not determine type of post to post to reddit: {p}")


def flairs_for_subdreddit(reddit: praw.Reddit, subreddit: str) -> List[rpc.Flair]:
    sub = reddit.subreddit(subreddit)
    flairs = [
        f for f in sub.flair.link_templates.user_selectable() if f["flair_text"] != ""
    ]
    return [rpc.Flair(text=f["flair_text"], id=f["flair_template_id"]) for f in flairs]


def simulate_post(post):
    log.info("Would've posted: %s", post)


def get_reddit(cfg):
    return praw.Reddit(
        client_id=cfg["ClientId"],
        client_secret=cfg["ClientSecret"],
        password=cfg["Password"],
        username=cfg["Username"],
        user_agent=f"desktop:{cfg['ClientId']}:v0.0.1  (by u/{cfg['Username']})",
    )


class Poster:
    """Routinely checks if any posts are eligible to be posted and then posts them to Reddit."""

    def __init__(self, reddit_config, dry_run: bool = True, step_interval: float = 5):
        self.dry_run = dry_run
        self.step_interval = step_interval
        self.reddit = get_reddit(reddit_config)

    def step(self):
        """Posts all eligible posts and marks them as posted in the datbase."""
        log.debug("Poster doing step")
        # Get the eligible posts from the database
        eligible = []  # type: List[rpc.PostDbEntry]
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
        posted = []  # type: List[rpc.PostDbEntry]
        for entry in eligible:
            if self.dry_run:
                simulate_post(entry.post)
                posted.append(entry)
            else:
                try:
                    post_to_reddit(self.reddit, entry)
                    posted.append(entry)
                except RedditAPIException as e:
                    msg = f"Failed to post post with id {entry.id}:"
                    report = []
                    for sube in e.items:
                        report.append(f"-> {sube.error_type}: {sube.message or ''}")
                    log.error("\n".join([msg] + report))
                    command = DbCommand(
                        "mark_error", ObjMarkError(entry.id, "\n".join(report))
                    )
                    self.db.queue_command(command)

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
    set_debug_level(logging.INFO)
    config = get_config()
    if config is None or not is_valid_config(config):
        sys.exit(1)
    general = config["General"]

    # Check for debugging
    if "Debug" in general and general["Debug"] == True:
        log.info("Debug logging enabled")
        set_debug_level(logging.DEBUG)

    # Start database
    db = Database(
        os.environ.get("DB_PATH")
        or os.path.expandvars("$HOME/.config/reddit-scheduler/database.sqlite")
    )
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
        Servicer().link_database(db).set_reddit_config(config["RedditAPI"]), server
    )
    addr = f"[::]:{general.getint('Port')}"
    server.add_insecure_port(addr)
    log.debug("Starting rpc server on %s", addr)
    log.info("Service started on %s", addr)

    server.start()
    daemon.notify("READY=1")
    server.wait_for_termination()
    db.queue_command(DbCommand(command="quit", obj=None))
