import grpc
import click
from dateutil import parser
from datetime import datetime
from tabulate import tabulate
import time
import os
import configparser
import json

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc

PROMPT = "> "
TIME_FMT = "%m/%d/%Y %I:%M %p"

TEST_POST = rpc.Post(
    title="Hello there",
    subreddit="test",
    body="sample body disregard",
    scheduled_time=int(time.time()),
)

CONFIG_SEARCH_PATHS = [
    os.path.expandvars("$HOME/.config/reddit-scheduler/config.ini"),
    "./config.ini",
]

ERR_MISSING_SERVICE = (
    "Failed to connect to service. Are you sure it's running and on the expected port?\n\n"
    "You can turn it on with\n"
    "$ systemctl --user start reddit-scheduler\n\n"
    "Or check for status with\n"
    "$ systemctl --user status reddit-scheduler\n\n"
    "Both service and client should use the port from the config.ini file unless changed "
    "via client flag."
)

ERR_MISSING_CONFIG = (
    "Could not find a config file to pull a port number from. "
    "Alternatively, you can specify it with the --port flag.\n\n"
    "Search path for the config file is as follows:\n"
)
for path in CONFIG_SEARCH_PATHS:
    ERR_MISSING_CONFIG += f"  - {path}\n"


class Config:
    def __init__(self, port):
        self.port = port


def make_post_from_cli():
    print("Title:")
    title = input(PROMPT).strip()
    print("Subreddit:")
    subreddit = input(PROMPT + "r/")
    print("Body (optional):")
    body = input(PROMPT)
    while True:
        print("Post time:")
        time_input = input(PROMPT)
        try:
            time = parser.parse(time_input, dayfirst=True)
        except ValueError:
            print("Could not parse time:", time_input)
            return None

        now = datetime.now()
        if time > now:
            break

        print("The time you entered is in the past:")
        print("Entered:", time.strftime(TIME_FMT))
        print("Current: ", now.strftime(TIME_FMT))
        print("Would you like to enter a new time? (y/n)")
        if input(PROMPT) != "y":
            return None

    return rpc.Post(
        title=title,
        subreddit=subreddit,
        body=body,
        scheduled_time=int(time.timestamp()),
    )


def make_post_from_file(file):
    try:
        file = json.load(file)
    except json.JSONDecodeError as e:
        print("Failed to read json file. Probably a syntax error:\n")
        print(e)
        return None
    for key in ["title", "subreddit", "body", "scheduled_time"]:
        if key not in file:
            print("JSON missing key:", key)
            print("See `reddit post --help` for file format")
            return None
    try:
        time = parser.parse(file["scheduled_time"], dayfirst=True)
    except ValueError:
        print("Invalid scheduled time in JSON file:", file["scheduled_time"])
        return None

    now = datetime.now()
    if time < now:
        print("The scheduled time from the JSON file is in the past:")
        print("JSON:", time.strftime(TIME_FMT))
        print("Current: ", now.strftime(TIME_FMT))
        print("Do you still want to continue? (y/n)")
        if input(PROMPT) != "y":
            return None

    return rpc.Post(
        title=file["title"],
        subreddit=file["subreddit"],
        body=file["body"],
        scheduled_time=int(time.timestamp()),
    )


def print_post_list(posts, filter):
    rows = []
    headers = ["Id", "Scheduled Time", "Subreddit", "Title", "Posted"]
    posts.sort(key=lambda entry: entry.post.scheduled_time, reverse=True)
    for entry in posts:
        if filter == "unposted" and entry.posted:
            continue
        if filter == "posted" and not entry.posted:
            continue
        row = []
        post = entry.post
        pretty_time = datetime.utcfromtimestamp(post.scheduled_time).strftime(TIME_FMT)
        row.append(entry.id)
        row.append(pretty_time)
        row.append(post.subreddit)
        row.append(post.title)
        row.append(entry.posted)
        rows.append(row)
    print(tabulate(rows, headers=headers))


def old_main():
    with grpc.insecure_channel("localhost:50051") as channel:
        stub = reddit_grpc.RedditSchedulerStub(channel)
        req = rpc.ListPostsRequest()
        reply = stub.ListPosts(req)
        for r in reply.posts:
            print(r)


@click.command()
@click.option("--file", type=click.File())
@click.pass_obj
def post(config, file):
    """Schedule a reddit post.
    If FILENAME is not provided, start an interactive prompt.
    Otherwise, FILENAME is a json file containing
    post information.

    The interactive prompt will ask for title, subreddit, body, and scheduled time.
    The body is optional, and scheduled time may contain any combination of
    date and time. Note that dates are US style: DD/MM.

    The format of the json file is as follows:
    {
        "title": "...",
        "subreddit": "...",
        "body": "",
        "scheduled_time": "12/21/2088 4:30 AM"
    }

    Empty string for 'body' means no body.
    """
    rpc_post = make_post_from_cli() if file is None else make_post_from_file(file)
    if rpc_post is None:
        return
    try:
        with grpc.insecure_channel(f"localhost:{config.port}") as channel:
            stub = reddit_grpc.RedditSchedulerStub(channel)
            reply = stub.SchedulePost(rpc_post)
            if reply.error_msg:
                print(
                    "Failed to schedule post. Server returned error:", reply.error_msg
                )
            else:
                print("Scheduled.")
    except grpc.RpcError:
        print(ERR_MISSING_SERVICE)


@click.command()
@click.option(
    "-f", "--filter", type=click.Choice(["all", "unposted", "posted"]), default="all"
)
@click.pass_obj
def list(config, filter):
    """Lists scheduled and completed posts."""
    try:
        with grpc.insecure_channel(f"localhost:{config.port}") as channel:
            stub = reddit_grpc.RedditSchedulerStub(channel)
            reply = stub.ListPosts(rpc.ListPostsRequest())
            if reply.error_msg:
                print("Failed to list posts. Server returned error:", reply.error_msg)
                return
            print_post_list(reply.posts, filter)
    except grpc.RpcError:
        print(ERR_MISSING_SERVICE)


@click.command()
@click.argument("post_id", type=int)
@click.pass_obj
def delete(config, post_id):
    """Delete a post.
    The POST_ID argument selects which post to delete. You can list ids with
    the `list` subcommand
    """
    click.confirm("Are you sure?", abort=True)
    try:
        with grpc.insecure_channel(f"localhost:{config.port}") as channel:
            stub = reddit_grpc.RedditSchedulerStub(channel)
            reply = stub.EditPost(
                rpc.EditPostRequest(operation=rpc.EditPostRequest.DELETE, id=post_id)
            )
            print("Deleted.")
            if reply.error_msg:
                print("Failed to delete post. Server returned error:", reply.error_msg)
                return
    except grpc.RpcError:
        print(ERR_MISSING_SERVICE)


@click.command()
@click.pass_obj
def debug(config):
    print(config.port)


def get_default_config_path():
    for path in CONFIG_SEARCH_PATHS:
        if os.path.exists(path):
            return path
    return None


@click.group()
@click.option("--config", default=get_default_config_path)
@click.option("--port", type=int, default=None)
@click.pass_context
def main(ctx, config, port):
    if port is None:
        if config is None:
            print(ERR_MISSING_CONFIG)
            ctx.abort()
        parser = configparser.ConfigParser()
        parser.read(config)
        try:
            port = parser["General"]["Port"]
        except KeyError:
            print("Could not find Port setting in", config)
            print("Please add it or use the --port flag")
            ctx.abort()
    ctx.obj = Config(port=port)
    pass


if __name__ == "__main__":
    main.add_command(post)
    main.add_command(list)
    main.add_command(delete)
    main.add_command(debug)
    main()
