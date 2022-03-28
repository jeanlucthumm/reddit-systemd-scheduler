import unittest
import yaml

from client import *

class ClientTest(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()
