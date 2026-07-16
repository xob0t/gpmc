import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gpmc.client import Client
from gpmc.db import Storage
from gpmc.models import MediaItem


class TestUploadFeatures(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for tests
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_storage.db"

        # Create some dummy files to "upload"
        self.file1 = Path(self.test_dir) / "image1.jpg"
        self.file1.write_bytes(b"dummy image data 1")

        self.file2 = Path(self.test_dir) / "image2.jpg"
        self.file2.write_bytes(b"dummy image data 2")

        # Clean up any existing failed_skipped_files.log
        self.log_file = Path("failed_skipped_files.log")
        if self.log_file.exists():
            self.log_file.unlink()

        # Mock auth data check to not raise ValueError
        with patch('gpmc.client.Client._handle_auth_data', return_value="androidId=123&app=com.google.android.apps.photos&Email=test@gmail.com"):
            self.client = Client(auth_data="dummy")

        # Override db_path and cache_dir to temp dir
        self.client.db_path = self.db_path
        self.client.cache_dir = Path(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir)
        if self.log_file.exists():
            self.log_file.unlink()

    def test_filename_exists(self):
        """Test filename_exists helper in Storage."""
        with Storage(self.db_path) as storage:
            # 1. Initially False
            self.assertFalse(storage.filename_exists("image1.jpg"))

            # 2. Insert remote media and test filename_exists
            item = MediaItem(
                media_key="key_1",
                file_name="image1.jpg",
                dedup_key="dedup_1",
                is_canonical=True,
                type=1,
                caption="",
                collection_id="",
                size_bytes=100,
                quota_charged_bytes=0,
                origin="",
                content_version=1,
                utc_timestamp=0,
                server_creation_timestamp=0,
                timezone_offset=0,
                width=100,
                height=100,
                remote_url="",
                upload_status=0,
                trash_timestamp=0,
                is_archived=0,
                is_favorite=0,
                is_locked=0,
                is_original_quality=1,
                latitude=0.0,
                longitude=0.0,
                location_name="",
                location_id="",
                is_edited=0,
                make="",
                model="",
                aperture=0.0,
                shutter_speed=0.0,
                iso=0,
                focal_length=0.0,
                duration=0,
                capture_frame_rate=0.0,
                encoded_frame_rate=0.0,
                is_micro_video=0,
                micro_video_width=0,
                micro_video_height=0
            )
            storage.update([item])
            self.assertTrue(storage.filename_exists("image1.jpg"))

    @patch('gpmc.client.Api')
    def test_duplicate_filename_fallback_skips_and_logs(self, mock_api_class):
        """Test that a file whose hash is unique but filename already exists is skipped and logged."""
        mock_api = mock_api_class.return_value
        # Hash is unique, so remote check returns None
        mock_api.find_remote_media_by_hash.return_value = None
        self.client.api = mock_api

        # Populate database cache with a remote item having filename "image1.jpg"
        with Storage(self.db_path) as storage:
            item = MediaItem(
                media_key="key_existing",
                file_name="image1.jpg",
                dedup_key="dedup_existing",
                is_canonical=True,
                type=1,
                caption="",
                collection_id="",
                size_bytes=100,
                quota_charged_bytes=0,
                origin="",
                content_version=1,
                utc_timestamp=0,
                server_creation_timestamp=0,
                timezone_offset=0,
                width=100,
                height=100,
                remote_url="",
                upload_status=0,
                trash_timestamp=0,
                is_archived=0,
                is_favorite=0,
                is_locked=0,
                is_original_quality=1,
                latitude=0.0,
                longitude=0.0,
                location_name="",
                location_id="",
                is_edited=0,
                make="",
                model="",
                aperture=0.0,
                shutter_speed=0.0,
                iso=0,
                focal_length=0.0,
                duration=0,
                capture_frame_rate=0.0,
                encoded_frame_rate=0.0,
                is_micro_video=0,
                micro_video_width=0,
                micro_video_height=0
            )
            storage.update([item])

        # 1. When skip_existing_filenames=False, it should NOT skip (proceeds to get upload token)
        mock_api.get_upload_token.return_value = "mock_token"
        mock_api.upload_file.return_value = {"mock": "response"}
        mock_api.commit_upload.return_value = "media_key_committed"
        res = self.client.upload(target=self.file1, skip_existing_filenames=False)
        self.assertIn(self.file1.absolute().as_posix(), res)

        # Reset log file
        if self.log_file.exists():
            self.log_file.unlink()

        # 2. When skip_existing_filenames=True, it should call update_cache and skip due to filename matching the DB
        with patch.object(self.client, 'update_cache') as mock_update_cache:
            res_skip = self.client.upload(target=self.file1, skip_existing_filenames=True)
            mock_update_cache.assert_called_once()
            self.assertEqual(res_skip, {})

        # Verify it was logged in failed_skipped_files.log
        self.assertTrue(self.log_file.exists())
        log_content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("SKIP", log_content)
        self.assertIn("image1.jpg", log_content)
        self.assertIn("Filename already exists in destination", log_content)

    @patch('gpmc.client.Api')
    def test_upload_failure_logged(self, mock_api_class):
        """Test that a file whose upload fails is logged in failed_skipped_files.log."""
        mock_api = mock_api_class.return_value
        mock_api.find_remote_media_by_hash.return_value = None
        # Make get_upload_token raise an exception to simulate failure
        mock_api.get_upload_token.side_effect = Exception("Network timeout")
        self.client.api = mock_api

        # Attempt uploading file1, which will fail
        self.client.upload(target=self.file1)

        # Verify it was logged in failed_skipped_files.log
        self.assertTrue(self.log_file.exists())
        log_content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("FAIL", log_content)
        self.assertIn("image1.jpg", log_content)
        self.assertIn("Network timeout", log_content)

if __name__ == "__main__":
    unittest.main()
