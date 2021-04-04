import grpc

import reddit_pb2
import reddit_pb2_grpc


def main():
    with grpc.insecure_channel("localhost:50051") as channel:
        stub = reddit_pb2_grpc.RedditSchedulerStub(channel)
        req = reddit_pb2.ListPostsRequest();
        reply = stub.ListPosts(req)
        for r in reply.posts:
            print(r)


if __name__ == "__main__":
    main()
