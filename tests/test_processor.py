"""Unit tests for the new architecture."""

import json
import os
import tempfile
from unittest.mock import Mock, patch, MagicMock



from ydiskarc.client import YandexDiskClient
from ydiskarc.downloader import ResourceDownloader
from ydiskarc.cmds.processor import (
    Project,
    yd_get_full,
    yd_get_and_store_dir,
    scan_directory_for_stats,
)


class TestClient:
    def test_validate_yandex_url_valid_directory(self):
        assert YandexDiskClient.validate_yandex_url("https://disk.yandex.ru/d/ABC123") is True

    def test_validate_yandex_url_invalid(self):
        assert YandexDiskClient.validate_yandex_url("https://example.com/file") is False


class TestDownloader:
    @patch("ydiskarc.client.YandexDiskClient._create_session_with_retries")
    @patch("ydiskarc.downloader.open", create=True)
    @patch("ydiskarc.downloader.os.path.exists")
    @patch("ydiskarc.downloader.os.rename")
    def test_get_file_basic(self, mock_rename, mock_exists, mock_open, mock_session_factory):
        mock_exists.return_value = False
        mock_session = MagicMock()
        mock_response = Mock()
        mock_response.headers = {}
        mock_response.iter_content.return_value = [b"file content"]
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response
        mock_session_factory.return_value = mock_session

        client = YandexDiskClient()
        # Override session to use our mock
        client.session = mock_session

        downloader = ResourceDownloader(client)

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file
            downloader.get_file("http://example.com/file.txt", filepath=tmpdir)

        mock_session.get.assert_called_once()
        mock_file.write.assert_called()


class TestYdGetFull:
    @patch("ydiskarc.cmds.processor.YandexDiskClient")
    @patch("ydiskarc.cmds.processor.ResourceDownloader")
    def test_yd_get_full_basic(self, mock_downloader_cls, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_meta_resp = Mock()
        mock_meta_resp.json.return_value = {"type": "file"}
        mock_meta_resp.raise_for_status = Mock()
        mock_client.get_resource_metadata.return_value = mock_meta_resp

        mock_client.get_download_link.return_value = "http://example.com/download"

        mock_downloader = MagicMock()
        mock_downloader_cls.return_value = mock_downloader

        with tempfile.TemporaryDirectory() as tmpdir:
            yd_get_full("https://disk.yandex.ru/d/test123", tmpdir, None, False)

        mock_client.get_resource_metadata.assert_called_once()
        mock_client.get_download_link.assert_called_once()
        mock_downloader.get_file.assert_called_once()


class TestYdGetAndStoreDir:
    @patch("ydiskarc.cmds.processor.YandexDiskClient")
    @patch("ydiskarc.cmds.processor.ResourceDownloader")
    def test_yd_get_and_store_dir_basic(self, mock_downloader_cls, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_resp = Mock()
        mock_resp.text = json.dumps({"name": "test_dir", "type": "dir", "_embedded": {"items": []}})
        mock_resp.json.return_value = {
            "name": "test_dir",
            "type": "dir",
            "_embedded": {"items": []},
        }
        mock_resp.raise_for_status = Mock()
        mock_client.get_resource_metadata.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            result = yd_get_and_store_dir(
                "https://disk.yandex.ru/d/test123",
                "",
                tmpdir,
                update=False,
                nofiles=True,
                iterative=False,
            )
            assert result is not None
            assert os.path.exists(os.path.join(tmpdir, "_metadata.json"))


class TestProject:
    @patch("builtins.print")
    @patch("ydiskarc.cmds.processor.scan_directory_for_stats")
    @patch("ydiskarc.cmds.processor.yd_get_and_store_dir")
    def test_project_sync(self, mock_yd_get, mock_scan, mock_print):
        mock_scan.return_value = (1, 100)
        project = Project()
        with tempfile.TemporaryDirectory() as tmpdir:
            project.sync("https://disk.yandex.ru/d/test123", tmpdir, False, False)
        mock_yd_get.assert_called_once()

    @patch("ydiskarc.cmds.processor.yd_get_full")
    def test_project_full(self, mock_yd_get):
        project = Project()
        with tempfile.TemporaryDirectory() as tmpdir:
            project.full("https://disk.yandex.ru/d/test123", tmpdir, None, False)
        mock_yd_get.assert_called_once()
