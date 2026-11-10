import sys
import unittest
from unittest.mock import patch

from gpmc.cli import main


class TestThreadsValidation(unittest.TestCase):
    """--threads must be rejected before any client work starts."""

    def _run_main(self, argv):
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as ctx:
                main()
        return ctx.exception.code

    def test_threads_zero_rejected(self):
        """--threads 0 exits 2 instead of crashing in ThreadPoolExecutor."""
        self.assertEqual(self._run_main(["gpmc", "--threads", "0", "some/photo.jpg"]), 2)

    def test_threads_negative_rejected(self):
        """Negative --threads exits 2 instead of crashing in ThreadPoolExecutor."""
        self.assertEqual(self._run_main(["gpmc", "--threads", "-3", "some/photo.jpg"]), 2)


if __name__ == "__main__":
    unittest.main()
