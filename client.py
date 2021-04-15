import grpc
import click
from dateutil import parser
from datetime import datetime
from tabulate import tabulate
import time
import json

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc

PROMPT = "> "
TIME_FMT = "%d/%m/%Y %I:%M %p"

TEST_POST = rpc.Post(
    title="Hello there",
    subreddit="test",
    body="sample body disregard",
    scheduled_time=int(time.time()),
)

ERR_MISSING_SERVICE = (
    "Failed to connect to service. Are you sure it's running and on the expected port?\n\n"
    "You can turn it on with\n"
    "$ systemctl --user start reddit-scheduler\n\n"
    "Or check for status with\n"
    "$ systemctl --user status reddit-scheduler\n\n"
    "Both service and client should use the port from the config.ini file unless changed "
    "via client flag."
)


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


def print_post_list(posts):
    rows = []
    headers = ["Id", "Scheduled Time", "Subreddit", "Title"]
    for post_with_id in posts:
        row = []
        post = post_with_id.post
        pretty_time = datetime.utcfromtimestamp(post.scheduled_time).strftime(TIME_FMT)
        row.append(post_with_id.id)
        row.append(pretty_time)
        row.append(post.subreddit)
        row.append(post.title)
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
@click.pass_context
def post(ctx, file):
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
        with grpc.insecure_channel("localhost:50051") as channel:
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
def list():
    """Lists scheduled and completed posts."""
    try:
        with grpc.insecure_channel("localhost:50051") as channel:
            stub = reddit_grpc.RedditSchedulerStub(channel)
            reply = stub.ListPosts(rpc.ListPostsRequest())
            if reply.error_msg:
                print(
                    "Failed to schedule post. Server returned error:", reply.error_msg
                )
                return
            print_post_list(reply.posts)

    except grpc.RpcError:
        print(ERR_MISSING_SERVICE)


@click.group()
def main():
    pass


if __name__ == "__main__":
    main.add_command(post)
    main.add_command(list)
    main()
