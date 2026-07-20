from pathlib import Path
import tempfile
import unittest

from iphone5dualbooter.idevicerestore_rebuild import _tail_build_log


class BuildLogTailTests(unittest.TestCase):
    def test_returns_requested_final_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "build.log"
            path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
            self.assertEqual(_tail_build_log(path, 2), "three\nfour")

    def test_missing_log_is_empty(self):
        self.assertEqual(_tail_build_log(Path("missing-build.log")), "")


if __name__ == "__main__":
    unittest.main()
