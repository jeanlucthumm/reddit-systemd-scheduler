import configparser
import os
import shutil
from datetime import datetime

import click
import grpc
import yaml
import questionary
from dateutil import parser
from tabulate import tabulate
from typing import Dict, List, Literal, Optional, TypeAlias
from colored import fg, attr

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc

PROMPT = "> "
TIME_FMT = "%m/%d/%Y %I:%M %p"

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

ERR_INVALID_POST_FILE = (
    "Parsing the YAML file for the post failed with the following error:\n\n"
)

ERR_MISSING_SAMPLE_POST_FILES = (
    "Could not find sample post files in /usr/share/doc/reddit-scheduler/.\n\n"
    "These should have been copied over automatically as part of the installation. "
    "Please file a bug report."
)

ERR_SAMPLE_CONFIG = (
    "Run `reddit file` to output a sample YAML post file in the current directory"
)

PostType: TypeAlias = Literal["text", "poll"]


class Config:
    def __init__(self, port):
        self.port = port


def validate_time(time_input: str) -> str | Literal[True]:
    try:
        time = parser.parse(time_input, dayfirst=False)
    except ValueError:
        return "Invalid time"
    now = datetime.now()
    if time < now:
        return "Time is in the past: " + time.strftime(TIME_FMT)
    return True


def validate_poll_duration(duration: str) -> str | Literal[True]:
    try:
        int(duration)
    except ValueError:
        return "Invalid duration"
    return True


def make_post_from_cli(stub: reddit_grpc.RedditSchedulerStub) -> rpc.Post | None:
    subreddit = questionary.text("Subreddit:").ask()
    if subreddit is None:
        return
    subreddit = subreddit.strip()

    title = questionary.text("Title:").ask()
    if title is None:
        return
    title = title.strip()

    type: PostType = questionary.select("Type of post:", choices=["text", "poll"]).ask()
    if type is None:
        return

    data = None
    if type == "text":
        body: str = questionary.text("Body:", multiline=True).ask()
        if body is None:
            return

        data = rpc.Data(text=rpc.TextPost(body=body))
    elif type == "poll":
        raw_options: str = questionary.text(
            "Options:", instruction="(Separate options by comma)"
        ).ask()
        if raw_options is None:
            return
        options = [x.strip() for x in raw_options.split(",")]

        selftext: str = questionary.text("Text content if any:", multiline=True).ask()
        if selftext is None:
            return

        duration = questionary.text(
            "Duration:",
            instruction="(Number of days poll should accept votes)",
            validate=validate_poll_duration,
        ).ask()
        if duration is None:
            return
        duration = int(duration)

        data = rpc.Data(
            poll=rpc.PollPost(selftext=selftext, duration=duration, options=options)
        )

    time = questionary.text(
        "Post time:",
        instruction="(Most formats work, dates are US style)",
        validate=validate_time,
    ).ask()
    if time is None:
        return
    time = parser.parse(time)

    flair_id = ""
    if questionary.confirm("Add flair to post?", default=False).ask():
        reply = stub.ListFlairs(rpc.ListFlairsRequest(subreddit=subreddit))
        flair_map: Dict[str, str] = {}
        for f in reply.flairs:
            flair_map[f.text] = f.id
        if len(flair_map) == 0:
          print(f"r/{subreddit} doesn't have any post flairs")
        else:
          selected = questionary.select(
              "Select flair:", choices=list(flair_map.keys())
          ).ask()
          flair_id = flair_map[selected]

    post = rpc.Post(
        title=title,
        subreddit=subreddit,
        scheduled_time=int(time.timestamp()),
        data=data,
        flair_id=flair_id,
    )
    return post


def verify_yaml_keys(file, keys: List[str]) -> bool:
    for key in keys:
        if key not in file:
            print("YAML missing key:", key)
            print(ERR_SAMPLE_CONFIG)
            return False
    return True


def make_post_from_text_yaml(file) -> rpc.TextPost | None:
    if not verify_yaml_keys(file, ["body"]):
        return None
    return rpc.TextPost(body=file["body"])


def make_post_from_poll_yaml(file) -> rpc.PollPost | None:
    if not verify_yaml_keys(file, ["options"]):
        return None
    post = rpc.PollPost(
        options=file["options"],
    )
    if "selftext" in file:
        post.selftext = file["selftext"]
    if "duration" in file:
        try:
            post.duration = int(file["duration"])
        except ValueError:
            print("Invalid duration in YAML file: ", file["duration"])
    return post


def make_post_from_file(stub: reddit_grpc.RedditSchedulerStub, path: str) -> rpc.Post | None:
    try:
        file = yaml.load(path, Loader=yaml.SafeLoader)
    except yaml.YAMLError as e:
        print(ERR_INVALID_POST_FILE, e)
        return None
    if not verify_yaml_keys(file, ["title", "subreddit", "type", "scheduled_time"]):
        return
    try:
        time = parser.parse(file["scheduled_time"], dayfirst=True)
    except ValueError:
        print("Invalid scheduled time in YAML file:", file["scheduled_time"])
        return None
    except TypeError:
        print(
            "Schedule time should be of type string, got:", type(file["scheduled_time"])
        )
        return None

    now = datetime.now()
    if time < now:
        print("The scheduled time from the YAML file is in the past:")
        print("YAML:", time.strftime(TIME_FMT))
        print("Current: ", now.strftime(TIME_FMT))
        print("It will be posted immediately. Do you still want to continue? (y/n)")
        if input(PROMPT) != "y":
            return None

    subreddit = file["subreddit"]
    flair_id = ""
    if "flair" in file:
        resp: rpc.ListFlairsResponse = stub.ListFlairs(
            rpc.ListFlairsRequest(subreddit=subreddit)
        )
        text = file["flair"]
        flair: Optional[rpc.Flair] = None
        for f in resp.flairs:
            if f.text == text:
                flair = f
                break
        if flair is None:
            print(f"r/{subreddit} doesn't have a flair called {flair}")
            return None
        flair_id = flair.id

    post_type = file["type"]
    data = rpc.Data()
    p = None
    if post_type == "poll":
        p = make_post_from_poll_yaml(file)
        if p is not None:
            data.poll.CopyFrom(p)
    elif post_type == "text":
        p = make_post_from_text_yaml(file)
        if p is not None:
            data.text.CopyFrom(p)
    else:
        print("Unknown post type: ", post_type)
        print(ERR_SAMPLE_CONFIG)
        return None
    if p is None:
        return None

    return rpc.Post(
        title=file["title"],
        subreddit=file["subreddit"],
        scheduled_time=int(time.timestamp()),
        data=data,
        flair_id=flair_id,
    )


def status_to_string(status) -> str:
    if status == rpc.PostStatus.PENDING:
        return "Pending"
    elif status == rpc.PostStatus.ERROR:
        return "Error"
    elif status == rpc.PostStatus.POSTED:
        return "Posted"
    else:
        return "Unknown"


def print_post_list(posts: List[rpc.PostDbEntry], filter: str):
    rows = []
    headers = ["Id", "Scheduled Time", "Subreddit", "Title", "Status"]
    posts.sort(key=lambda entry: entry.post.scheduled_time, reverse=True)
    error_id = None
    for entry in posts:
        if filter == "unposted" and entry.status == rpc.PostStatus.POSTED:
            continue
        if filter == "posted" and not entry.status == rpc.PostStatus.POSTED:
            continue
        if entry.status == rpc.PostStatus.ERROR and error_id is None:
            error_id = entry.id
        row = []
        post = entry.post
        pretty_time = datetime.fromtimestamp(post.scheduled_time).strftime(TIME_FMT)
        row.append(entry.id)
        row.append(pretty_time)
        row.append(post.subreddit)
        row.append(post.title)
        row.append(status_to_string(entry.status))
        rows.append(row)
    print(tabulate(rows, headers=headers))
    print()
    if error_id is not None:
        print(f"A post errored, use `reddit list -p {error_id}` to see why")


def print_post_info(all_posts: List[rpc.PostDbEntry], post_id: int):
    entry = None
    for p in all_posts:
        if p.id == post_id:
            entry = p
    if entry is None:
        print(f"No post with id {post_id}.")
        return
    post = entry.post
    rows = [
        ["Title", post.title],
        ["Subreddit", post.subreddit],
        [
            "Scheduled time",
            datetime.fromtimestamp(post.scheduled_time).strftime(TIME_FMT),
        ],
    ]
    if post.data.HasField("text"):
        rows.append(["Body", post.data.text.body])
    if post.data.HasField("poll"):
        poll = post.data.poll
        rows += [
            ["Selftext", poll.selftext],
            ["Duration", poll.duration],
        ]
        opt_text = ""
        for opt in poll.options:
            opt_text += f"-- {opt}\n"
        rows.append(["Options", opt_text])
    print(tabulate(rows))
    if entry.status == rpc.PostStatus.ERROR:
        print(f"{fg('red')}Posting failed with error:\n{entry.error}{attr('reset')}")


@click.command()
@click.option("-f", "--file", type=click.File())
@click.pass_obj
def post(config, file):
    """Schedule a reddit post.

    Default behavior is an interactive CLI. If FILENAME is provided, then post
    information will be sourced from there. Use `reddit file` to generate
    boilerplate post yaml files which can be filled in.
    """
    try:
        with grpc.insecure_channel(f"[::]:{config.port}") as channel:
            stub = reddit_grpc.RedditSchedulerStub(channel)
            rpc_post = (
                make_post_from_cli(stub) if file is None else make_post_from_file(stub, file)
            )
            if rpc_post is None:
                return
            reply = stub.SchedulePost(rpc_post)
            if reply.error_msg:
                print(
                    "Failed to schedule post. Server returned error:", reply.error_msg
                )
            else:
                print("Scheduled.")
    except grpc.RpcError as e:
        print(ERR_MISSING_SERVICE)
        print(e)


@click.command()
@click.option("-t", "--type", required=True, type=click.Choice(["text", "poll"]))
def file(type):
    """Create a sample post file of the given type.

    These can be filled in and then used with `reddit post -f FILENAME`.
    """
    try:
        if type == "text":
            shutil.copyfile(
                "/usr/share/doc/reddit-scheduler/examples/text-post.yaml",
                "text-post.yaml",
            )
            print("./text-post.yaml created.")
        elif type == "poll":
            shutil.copyfile(
                "/usr/share/doc/reddit-scheduler/examples/poll-post.yaml",
                "poll-post.yaml",
            )
            print("./poll-post.yaml created.")
        else:
            assert False
    except FileNotFoundError:
        print(ERR_MISSING_SAMPLE_POST_FILES)


@click.command(name="list")
@click.option(
    "-f", "--filter", type=click.Choice(["all", "unposted", "posted"]), default="all"
)
@click.option("-p", "--post_id", type=int)
@click.pass_obj
def list_posts(config, filter, post_id):
    """List information about post(s).
    If -p option is given, lists detailed information about the post with that
    ID. Otherwise, lists all posts filtered with the -f option.
    """
    try:
        with grpc.insecure_channel(f"[::]:{config.port}") as channel:
            stub = reddit_grpc.RedditSchedulerStub(channel)
            reply = stub.ListPosts(rpc.ListPostsRequest())
            if reply.error_msg:
                print("Failed to list posts. Server returned error:", reply.error_msg)
                return
            if post_id is None:
                print_post_list(reply.posts, filter)
            else:
                print_post_info(reply.posts, post_id)
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
        with grpc.insecure_channel(f"[::]:{config.port}") as channel:
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


def get_default_config_path():
    for path in CONFIG_SEARCH_PATHS:
        if os.path.exists(path):
            return path
    return None


@click.group()
@click.option("--config", type=str, default=get_default_config_path)
@click.option("--port", type=int, default=None)
@click.pass_context
def main(ctx, config, port):
    """CLI for reddit scheduler service."""
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
    main.add_command(file)
    main.add_command(list_posts)
    main.add_command(delete)
    main()
