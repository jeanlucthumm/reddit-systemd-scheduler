import unittest
import yaml
import grpc

from client import *
from grpc.framework.foundation import logging_pool
from click.testing import CliRunner
import reddit_pb2 as proto
import reddit_pb2_grpc

PORT = 5071


class MockGoodServicer(reddit_pb2_grpc.RedditSchedulerServicer):
    def ListPosts(self, request, _):
        del request
        return proto.ListPostsReply()

    def ListFlairs(self, request: proto.ListFlairsRequest, _):
        flairs = []
        if request.subreddit == "test":
            flairs = [
                proto.Flair(text="flair1", id="1"),
                proto.Flair(text="flair2", id="2"),
            ]
        return proto.ListFlairsResponse(flairs=flairs)

    def SchedulePost(self, request, _):
        del request
        return proto.SchedulePostReply(error_msg="fail")

    def EditPost(self, request, _):
        del request
        return proto.EditPostReply()


class ClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = logging_pool.pool(5)
        self.server = grpc.server(self.pool)
        addr = f"[::]:{PORT}"
        self.server.add_insecure_port(addr)
        reddit_pb2_grpc.add_RedditSchedulerServicer_to_server(
            MockGoodServicer(), self.server
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop(None)
        self.pool.shutdown(wait=True)

    def test_make_post_from_poll_yaml(self):
        # TODO add error cases once we switch to logging instead of print
        f = open("examples/poll-post.yaml", "r")
        file = yaml.safe_load(f)
        f.close()
        ret = make_post_from_poll_yaml(file)
        self.assertIsNotNone(ret)
        if ret is not None:
            self.assertEqual(ret.selftext, "")
            self.assertEqual(ret.duration, 7)
            self.assertEqual(ret.options, ["Yes", "No"])

    def test_post_flair(self):
        runner = CliRunner()
        main.add_command(post)

        result = runner.invoke(
            main,
            ["--port", str(PORT), "post", "-f", "testdata/text-post.yaml"],
            input="y\n"
        )
        if result.exit_code != 0:
            print(result.stdout)
        assert result.exit_code == 0


if __name__ == "__main__":
    unittest.main()
