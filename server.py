from concurrent import futures

import grpc

import reddit_pb2 as rpc
import reddit_pb2_grpc as reddit_grpc

class RedditScheduler(reddit_grpc.RedditSchedulerServicer):
    def ListPosts(self, request, context):
        print('Got list posts RPC')
        posts = ["Hello", "There", "How"]
        return rpc.ListPostsReply(posts=posts)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    reddit_grpc.add_RedditSchedulerServicer_to_server(
        RedditScheduler(), server
    )
    server.add_insecure_port('[::]:50051')
    print('Starting server...')
    server.start()
    server.wait_for_termination()

if __name__ == '__main__':
    serve()
