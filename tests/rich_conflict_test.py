import unittest

from rich.errors import LiveError
from rich.live import Live

from gpmc import Client


class TestUpload(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.image_file_path = "media/image.png"

    def test_rich_live_no_conflict(self):
        """Test conflict"""
        with Live():
            self.client.upload(target=self.image_file_path, show_progress=False)

    def test_rich_live_conflict(self):
        """Test conflict"""
        with self.assertRaises(LiveError), Live():
            self.client.upload(target=self.image_file_path, show_progress=True)


if __name__ == "__main__":
    unittest.main()
