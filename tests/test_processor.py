"""Unit and integration tests for the client, the downloader and the sync engine."""

import hashlib
import json
import os
import threading

import pytest

from tests.fake_yandex import MODIFIED_TS, FakeYandexDisk
from ydiskarc.client import YandexDiskClient, parse_public_url
from ydiskarc.cmds.processor import Project, yd_get_full
from ydiskarc.config import config
from ydiskarc.downloader import DownloadError, ResourceDownloader
from ydiskarc.progress import Reporter, format_summary
from ydiskarc.utils import sanitize_filename

URL = "https://disk.yandex.ru/d/TestKey123"
# Quotes are not allowed in Windows file names, so there the safe names are tested.
SAFE = os.name == "nt"

TREE = {
    "/a.txt": b"hello",
    '/Папка/b "quoted".bin': os.urandom(1000),
    "/Папка/c%3Fd.txt": b"percent name",
    "/Папка/Sub/deep.dat": os.urandom(300 * 1024),
    "/empty/": None,
}


@pytest.fixture
def fast(monkeypatch):
    """No pauses between retries, small listing pages to exercise pagination."""
    for name, value in [
        ("api_retry_delay", 0),
        ("download_retry_delay", 0),
        ("retry_round_pause", 0),
        ("retry_backoff_factor", 0),
        ("list_page_size", 2),
        ("read_timeout", 5),
        ("chunk_size", 64 * 1024),
        # deep.dat (300 KB) is downloaded in 3 segments, the other files in one stream
        ("segment_size", 100 * 1024),
        ("min_segment_size", 100 * 1024),
    ]:
        monkeypatch.setattr(config, name, value)


@pytest.fixture
def disk(fast, monkeypatch):
    with FakeYandexDisk(dict(TREE)) as fake:
        monkeypatch.setattr(config, "api_base_url", fake.base + "/v1/disk")
        yield fake


class Recorder(Reporter):
    def __init__(self):
        self.messages = []
        self.ends = []
        self.progress = 0
        self.lock = threading.Lock()

    def file_progress(self, task, nbytes):
        with self.lock:
            self.progress += nbytes

    def file_end(self, task, status, error=None):
        with self.lock:
            self.ends.append((task.display, status))

    def message(self, level, text):
        self.messages.append((level, text))


def local(root, remote_path):
    """Local path of a remote file, as the sync engine names it."""
    parts = [sanitize_filename(p, SAFE) for p in remote_path.strip("/").split("/")]
    return root.joinpath(*parts)


def sync(output, url=URL, **kwargs):
    kwargs.setdefault("safe_names", SAFE)
    kwargs.setdefault("threads", 2)
    return Project().sync(url, str(output), **kwargs)


def dl_requests(disk):
    return [r for r in disk.requests if r[0] == "/dl"]


class TestParseUrl:
    def test_root(self):
        parsed = parse_public_url("https://disk.yandex.ru/d/ABC123")
        assert parsed.public_key == "https://disk.yandex.ru/d/ABC123"
        assert parsed.path == ""

    def test_subfolder_percent_encoded(self):
        parsed = parse_public_url(
            "https://disk.yandex.ru/d/ABC123/%D0%9F%D0%B0%D0%BF%D0%BA%D0%B0/Sub%20dir/"
        )
        assert parsed.public_key == "https://disk.yandex.ru/d/ABC123"
        assert parsed.path == "/Папка/Sub dir"

    def test_subfolder_pretty(self):
        parsed = parse_public_url("disk.yandex.com/d/ABC123/Папка/Sub dir?w=1")
        assert parsed.public_key == "https://disk.yandex.ru/d/ABC123"
        assert parsed.path == "/Папка/Sub dir"

    def test_other_hosts(self):
        assert parse_public_url("https://yadi.sk/d/XyZ").public_key == "https://yadi.sk/d/XyZ"
        assert parse_public_url("https://disk.360.yandex.ru/i/XyZ").kind == "i"

    def test_invalid(self):
        assert parse_public_url("https://example.com/d/ABC") is None
        assert parse_public_url("not-a-url") is None
        assert YandexDiskClient.validate_yandex_url("https://disk.yandex.ru/d/ABC123") is True
        assert YandexDiskClient.validate_yandex_url("https://example.com/file") is False


class TestSanitize:
    def test_safe_names(self):
        assert sanitize_filename('a "b" c?.txt', True) == "a ＂b＂ c？.txt"
        assert sanitize_filename("con.txt", True) == "_con.txt"
        assert sanitize_filename("name. ", True) == "name__"

    def test_native_names_keep_everything(self):
        assert sanitize_filename('a "b" c?.txt', False) == 'a "b" c?.txt'

    def test_percent_is_never_decoded(self):
        assert sanitize_filename("c%3Fd.txt", True) == "c%3Fd.txt"
        assert sanitize_filename("c%3Fd.txt", False) == "c%3Fd.txt"

    def test_long_names_are_shortened_uniquely(self):
        a = sanitize_filename("я" * 200 + "a.txt", False)
        b = sanitize_filename("я" * 200 + "b.txt", False)
        assert a != b
        assert a.endswith(".txt")
        if os.name == "nt":
            assert len(a) <= 255 - 16
        else:
            assert len(a.encode("utf-8")) <= 255 - 16


class TestSync:
    def test_full_sync(self, disk, tmp_path):
        stats = sync(tmp_path)
        assert stats.ok, format_summary(stats)
        for path, content in TREE.items():
            target = local(tmp_path, path)
            if content is None:
                assert target.is_dir()
                continue
            assert target.read_bytes() == content
            assert abs(target.stat().st_mtime - MODIFIED_TS) < 1
        assert stats.downloaded_files == 4
        assert stats.verified_files == 4
        leftovers = [
            name
            for _, _, names in os.walk(tmp_path)
            for name in names
            if name.endswith(config.part_suffix) or name == "_metadata.json"
        ]
        assert leftovers == []

    def test_second_run_downloads_nothing(self, disk, tmp_path):
        sync(tmp_path)
        before = len(dl_requests(disk))
        stats = sync(tmp_path)
        assert stats.ok
        assert stats.skipped_files == 4
        assert stats.downloaded_files == 0
        assert len(dl_requests(disk)) == before

    def test_changed_files_are_downloaded_again(self, disk, tmp_path):
        sync(tmp_path)
        same_size = tmp_path / "a.txt"
        same_size.write_bytes(b"HELLO")  # same size, new mtime -> checksum is checked
        other_size = tmp_path / "Папка" / "c%3Fd.txt"
        other_size.write_bytes(b"short")
        stats = sync(tmp_path)
        assert stats.downloaded_files == 2
        assert same_size.read_bytes() == b"hello"
        assert other_size.read_bytes() == b"percent name"

    def test_same_size_and_date_is_trusted_without_verify(self, disk, tmp_path):
        sync(tmp_path)
        local = tmp_path / "a.txt"
        local.write_bytes(b"HELLO")
        os.utime(local, (MODIFIED_TS, MODIFIED_TS))
        assert sync(tmp_path).downloaded_files == 0
        assert sync(tmp_path, verify=True).downloaded_files == 1
        assert local.read_bytes() == b"hello"

    def test_error_is_reported_not_written_into_file(self, disk, tmp_path):
        disk.fail_download["/a.txt"] = 1000
        recorder = Recorder()
        stats = sync(tmp_path, retries=1, reporter=recorder)
        assert not stats.ok
        assert list(stats.failed_files) == ["a.txt"]
        assert not (tmp_path / "a.txt").exists()
        assert (tmp_path / "Папка" / "c%3Fd.txt").exists()
        assert any(level == "warning" and "a.txt" in text for level, text in recorder.messages)
        summary = "\n".join(line for _, line in format_summary(stats))
        assert "Files not downloaded: 1" in summary
        assert "a.txt" in summary

    def test_transient_errors_are_retried_and_resumed(self, disk, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "min_segment_size", 1024 * 1024)  # single-stream downloads
        disk.fail_download["/a.txt"] = 2
        disk.truncate_once.add("/Папка/Sub/deep.dat")
        disk.corrupt_once.add('/Папка/b "quoted".bin')
        recorder = Recorder()
        stats = sync(tmp_path, reporter=recorder)
        assert stats.ok, format_summary(stats)
        assert stats.resumed_files == 1
        ranges = [r for r in dl_requests(disk) if r[2]]
        assert ranges and ranges[0][1]["path"] == "/Папка/Sub/deep.dat"
        assert (tmp_path / "Папка" / "Sub" / "deep.dat").read_bytes() == TREE["/Папка/Sub/deep.dat"]
        quoted = '/Папка/b "quoted".bin'
        assert local(tmp_path, quoted).read_bytes() == TREE[quoted]
        assert recorder.progress == sum(len(v) for v in TREE.values() if v)

    def test_dead_connection_does_not_hang(self, disk, tmp_path, monkeypatch):
        """A connection that goes silent (e.g. after sleep mode) times out and is resumed."""
        monkeypatch.setattr(config, "read_timeout", 1)
        disk.stall_once.add("/Папка/Sub/deep.dat")
        stats = sync(tmp_path)
        assert stats.ok, format_summary(stats)
        assert stats.resumed_files == 1
        assert (tmp_path / "Папка" / "Sub" / "deep.dat").read_bytes() == TREE["/Папка/Sub/deep.dat"]

    def test_no_server_does_not_crash(self, fast, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "api_base_url", "http://127.0.0.1:9/v1/disk")
        monkeypatch.setattr(config, "api_attempts", 2)
        monkeypatch.setattr(config, "connect_timeout", 1)
        stats = sync(tmp_path, retries=0)
        assert not stats.ok
        assert stats.error and "no response" in stats.error

    def test_checksum_mismatch_fails(self, disk, tmp_path):
        disk.wrong_sha.add("/a.txt")
        stats = sync(tmp_path, retries=0)
        assert "a.txt" in stats.failed_files
        assert "checksum" in stats.failed_files["a.txt"]
        assert not (tmp_path / "a.txt").exists()
        assert not (tmp_path / ("a.txt" + config.part_suffix)).exists()

    def test_folder_listing_failure_is_retried(self, disk, tmp_path):
        disk.fail_listing["/Папка"] = config.api_attempts  # the whole first pass fails
        stats = sync(tmp_path, retries=1)
        assert stats.ok, format_summary(stats)
        assert (tmp_path / "Папка" / "Sub" / "deep.dat").exists()

    def test_metadata_only(self, disk, tmp_path):
        stats = sync(tmp_path, nofiles=True)
        assert stats.ok
        assert not (tmp_path / "a.txt").exists()
        meta = json.loads((tmp_path / "_metadata.json").read_text(encoding="utf8"))
        # pages of 2 items were merged
        assert len(meta["_embedded"]["items"]) == 3
        assert (tmp_path / "Папка" / "Sub" / "_metadata.json").exists()
        assert stats.metadata_files == 4

    def test_subfolder_link(self, disk, tmp_path):
        url = URL + "/%D0%9F%D0%B0%D0%BF%D0%BA%D0%B0"
        stats = sync(tmp_path, url=url)
        assert stats.ok
        assert stats.total_files == 3
        assert (tmp_path / "Sub" / "deep.dat").exists()
        assert not (tmp_path / "a.txt").exists()
        pretty = sync(tmp_path / "pretty", url=URL + "/Папка/Sub")
        assert pretty.total_files == 1
        assert (tmp_path / "pretty" / "deep.dat").exists()

    def test_flat_and_safe_names(self, disk, tmp_path):
        stats = sync(tmp_path, flat=True, safe_names=True)
        assert stats.ok
        names = sorted(p.name for p in tmp_path.iterdir())
        assert names == ["a.txt", "b ＂quoted＂.bin", "c%3Fd.txt", "deep.dat"]

    def test_cancel(self, disk, tmp_path):
        event = threading.Event()
        event.set()
        stats = sync(tmp_path, cancel_event=event)
        assert stats.cancelled
        assert not stats.ok


class TestDownloader:
    def _downloader(self):
        return ResourceDownloader(YandexDiskClient())

    def test_resume_existing_part(self, disk, tmp_path):
        content = TREE["/Папка/Sub/deep.dat"]
        dest = tmp_path / "deep.dat"
        (tmp_path / ("deep.dat" + config.part_suffix)).write_bytes(content[:1000])
        result = self._downloader().download(
            disk.base + "/dl?path=/%D0%9F%D0%B0%D0%BF%D0%BA%D0%B0/Sub/deep.dat",
            str(dest),
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        assert result.resumed and result.verified
        assert result.downloaded == len(content) - 1000
        assert dest.read_bytes() == content

    def test_server_ignoring_range_restarts(self, disk, tmp_path):
        disk.ignore_range = True
        content = TREE["/a.txt"]
        dest = tmp_path / "a.txt"
        (tmp_path / ("a.txt" + config.part_suffix)).write_bytes(b"he")
        result = self._downloader().download(
            disk.base + "/dl?path=/a.txt",
            str(dest),
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        assert not result.resumed
        assert dest.read_bytes() == content

    def test_empty_file(self, disk, tmp_path):
        disk.files["/empty.txt"] = b""
        dest = tmp_path / "sub" / "empty.txt"
        result = self._downloader().download(
            None,
            str(dest),
            size=0,
            sha256=hashlib.sha256(b"").hexdigest(),
            refresh_url=lambda: disk.base + "/dl?path=/empty.txt",
        )
        assert result.verified
        assert dest.read_bytes() == b""

    def _deep(self, disk):
        content = TREE["/Папка/Sub/deep.dat"]
        url = disk.base + "/dl?path=/%D0%9F%D0%B0%D0%BF%D0%BA%D0%B0/Sub/deep.dat"
        return content, url, hashlib.sha256(content).hexdigest()

    def test_segmented_download(self, disk, tmp_path):
        content, url, sha = self._deep(disk)
        dest = tmp_path / "deep.dat"
        downloader = ResourceDownloader(YandexDiskClient(), connections=3)
        result = downloader.download(url, str(dest), size=len(content), sha256=sha)
        assert result.verified and not result.resumed
        assert dest.read_bytes() == content
        ranges = sorted(r[2] for r in dl_requests(disk))
        assert ranges == ["bytes=0-102399", "bytes=102400-204799", "bytes=204800-307199"]
        assert not (tmp_path / ("deep.dat" + config.part_suffix + ".state")).exists()

    def test_segmented_resume_from_state(self, disk, tmp_path):
        content, url, sha = self._deep(disk)
        part = tmp_path / ("deep.dat" + config.part_suffix)
        # first segment complete, second half done, third not started
        data = bytearray(len(content))
        data[:102400] = content[:102400]
        data[102400:153600] = content[102400:153600]
        part.write_bytes(bytes(data))
        state = {
            "size": len(content),
            "segments": [[0, 102400, 102400], [102400, 204800, 51200], [204800, 307200, 0]],
        }
        (tmp_path / ("deep.dat" + config.part_suffix + ".state")).write_text(json.dumps(state))
        dest = tmp_path / "deep.dat"
        downloader = ResourceDownloader(YandexDiskClient(), connections=2)
        result = downloader.download(url, str(dest), size=len(content), sha256=sha)
        assert result.resumed
        assert result.downloaded == len(content) - 153600
        assert dest.read_bytes() == content
        assert sorted(r[2] for r in dl_requests(disk)) == [
            "bytes=153600-204799",
            "bytes=204800-307199",
        ]

    def test_segmented_continues_single_stream_part(self, disk, tmp_path):
        content, url, sha = self._deep(disk)
        (tmp_path / ("deep.dat" + config.part_suffix)).write_bytes(content[:150000])
        dest = tmp_path / "deep.dat"
        result = ResourceDownloader(YandexDiskClient(), connections=4).download(
            url, str(dest), size=len(content), sha256=sha
        )
        assert result.resumed
        assert result.downloaded == len(content) - 150000
        assert dest.read_bytes() == content

    def test_segmented_falls_back_without_range_support(self, disk, tmp_path):
        disk.ignore_range = True
        content, url, sha = self._deep(disk)
        dest = tmp_path / "deep.dat"
        progress = []
        result = ResourceDownloader(YandexDiskClient(), connections=3).download(
            url, str(dest), size=len(content), sha256=sha, on_progress=progress.append
        )
        assert result.verified
        assert dest.read_bytes() == content
        assert sum(progress) == len(content)

    def test_segmented_broken_connection(self, disk, tmp_path):
        content, url, sha = self._deep(disk)
        disk.truncate_once.add("/Папка/Sub/deep.dat")
        dest = tmp_path / "deep.dat"
        progress = []
        result = ResourceDownloader(YandexDiskClient(), connections=3).download(
            url, str(dest), size=len(content), sha256=sha, on_progress=progress.append
        )
        assert result.verified
        assert dest.read_bytes() == content
        assert sum(progress) == len(content)

    def test_segmented_corrupt_data_is_redownloaded(self, disk, tmp_path):
        content, url, sha = self._deep(disk)
        disk.corrupt_once.add("/Папка/Sub/deep.dat")
        dest = tmp_path / "deep.dat"
        result = ResourceDownloader(YandexDiskClient(), connections=3).download(
            url, str(dest), size=len(content), sha256=sha
        )
        assert result.verified
        assert dest.read_bytes() == content

    def test_http_error_raises(self, disk, tmp_path):
        with pytest.raises(DownloadError):
            self._downloader().download(disk.base + "/dl?path=/missing", str(tmp_path / "x"))
        assert not (tmp_path / "x").exists()


class TestFull:
    def test_single_file(self, disk, tmp_path):
        stats = yd_get_full(URL + "/a.txt", str(tmp_path), None, True)
        assert stats.ok, format_summary(stats)
        assert (tmp_path / "a.txt").read_bytes() == b"hello"
        assert (tmp_path / "_metadata.json").exists()
        again = yd_get_full(URL + "/a.txt", str(tmp_path), None, False)
        assert again.skipped_files == 1

    def test_folder_as_zip(self, disk, tmp_path):
        stats = yd_get_full(URL, str(tmp_path), None, False)
        assert stats.ok, format_summary(stats)
        assert (tmp_path / "dump.zip").exists()
