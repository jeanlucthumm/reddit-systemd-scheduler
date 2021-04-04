from concurrent import futures

import grpc
import configparser
import praw
import time

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc

# <platform>:<app ID>:<version string> (by u/<Reddit username>)


class Servicer(reddit_grpc.RedditSchedulerServicer):
    def ListPosts(self, request, context):
        print("Got list posts RPC")
        posts = ["Hello", "There", "How"]
        return rpc.ListPostsReply(posts=posts)


class Poster:
    def __init__(self, config_path):
        self.config = configparser.ConfigParser()
        self.config.read(config_path)

        cfg = self.config["RedditAPI"]
        self.reddit = praw.Reddit(
            client_id=cfg["ClientId"],
            client_secret=cfg["ClientSecret"],
            password=cfg["Password"],
            username=cfg["Username"],
            user_agent=f"desktop:{cfg['ClientId']}:v0.0.1  (by u/{cfg['Username']})",
        )

    def start(self):
        post = rpc.Post(
            title="Hello there",
            subreddit="test",
            body="sample body disregard",
            scheduled_time=int(time.time()),
        )
        post_to_reddit(self.reddit, post)


def post_to_reddit(reddit, post):
    print('Posting to subreddit')
    subreddit = reddit.subreddit(post.subreddit)
    subreddit.submit(title=post.title, selftext=post.body, url=None)
    print('Submitted')


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    reddit_grpc.add_RedditSchedulerServicer_to_server(Servicer(), server)
    server.add_insecure_port("[::]:50051")
    print("Starting server...")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    s = Poster("config.ini")
    s.start()
