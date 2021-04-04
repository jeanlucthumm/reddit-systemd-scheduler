import grpc

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc


def main():
    with grpc.insecure_channel("localhost:50051") as channel:
        stub = reddit_grpc.RedditSchedulerStub(channel)
        req = rpc.ListPostsRequest()
        reply = stub.ListPosts(req)
        for r in reply.posts:
            print(r)


if __name__ == "__main__":
    main()
