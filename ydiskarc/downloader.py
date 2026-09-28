import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from typing import Callable, List, Optional
from urllib.parse import unquote

import requests

from .client import YandexDiskClient
from .config import config
from .utils import Cancelled, format_size, fs_path, new_hasher, set_mtime

__all__ = [
    "DownloadError",
    "HttpStatusError",
    "IncompleteDownload",
    "ResourceDownloader",
    "VerificationError",
    "format_size",
]

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int], None]


class DownloadError(Exception):
    """A file could not be downloaded."""


class HttpStatusError(DownloadError):
    def __init__(self, status: int):
        super().__init__(f"server returned HTTP {status}")
        self.status = status


class IncompleteDownload(DownloadError):
    """Connection ended before the whole file was received (the partial file is kept)."""


class VerificationError(DownloadError):
    """Downloaded data does not match the size or checksum reported by the API."""


class RangeNotSupported(DownloadError):
    """The server ignores Range requests, so the file cannot be downloaded in segments."""


STATE_SUFFIX = ".state"


@dataclass
class DownloadResult:
    path: str
    size: int
    downloaded: int  # bytes received over the network
    resumed: bool
    verified: bool  # checksum was checked


def _filename_from_headers(resp: requests.Response) -> Optional[str]:
    header = resp.headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=(?:UTF-8|utf-8)''([^;]+)", header)
    if match:
        return unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename="?([^";]+)"?', header)
    if match:
        try:
            return match.group(1).encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return match.group(1)
    return None


class ResourceDownloader:
    """Downloads files safely.

    * data is written to ``<name>.ydpart`` and renamed only after it has been verified;
    * interrupted downloads are resumed with HTTP Range requests;
    * the size and the SHA-256 (or MD5) reported by the API are checked, including the
      part that was downloaded before a resume;
    * HTTP errors are never written into the file - they raise :class:`DownloadError`;
    * network errors and stalled connections are retried, refreshing the download link.
    """

    def __init__(
        self,
        client: YandexDiskClient,
        verbose: bool = False,
        attempts: Optional[int] = None,
        connections: Optional[int] = None,
    ):
        self.client = client
        self.verbose = verbose
        self.attempts = attempts or config.download_attempts
        self.connections = max(1, connections or config.connections_per_file)

    def download(
        self,
        url: Optional[str],
        dest: str,
        size: Optional[int] = None,
        sha256: Optional[str] = None,
        md5: Optional[str] = None,
        mtime: Optional[float] = None,
        refresh_url: Optional[Callable[[], str]] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        """Download ``url`` to ``dest``. Raises :class:`DownloadError` or :class:`Cancelled`."""
        part = dest + config.part_suffix
        last_error: Exception = DownloadError("no download link")
        for attempt in range(1, self.attempts + 1):
            self.client.check_cancel()
            if attempt > 1 or not url:
                if attempt > 1:
                    self.client.reset_session()
                    self.client.sleep(min(config.download_retry_delay * 2 ** (attempt - 2), 120))
                if refresh_url is not None:
                    try:
                        url = refresh_url()
                    except Cancelled:
                        raise
                    except Exception as e:
                        last_error = e
                        logger.debug("Failed to refresh download link for %s: %s", dest, e)
                        continue
            if not url:
                continue
            reported = [0]
            track_lock = threading.Lock()

            def track(n: int) -> None:
                with track_lock:
                    reported[0] += n
                if on_progress is not None:
                    on_progress(n)

            try:
                return self._download_once(url, dest, part, size, sha256, md5, mtime, track)
            except BaseException as e:
                if not isinstance(e, Exception) or isinstance(e, Cancelled):
                    raise
                # Whatever this attempt reported is taken back; a retry reports it again.
                if reported[0] and on_progress is not None:
                    on_progress(-reported[0])
                last_error = e
                if isinstance(e, HttpStatusError) and e.status in (404, 410) and not refresh_url:
                    break
                if not isinstance(e, (DownloadError, requests.RequestException, OSError)):
                    raise
            logger.debug(
                "Attempt %s/%s for %s failed: %s", attempt, self.attempts, dest, last_error
            )
        raise DownloadError(str(last_error)) from last_error

    def _download_once(
        self,
        url: str,
        dest: str,
        part: str,
        size: Optional[int],
        sha256: Optional[str],
        md5: Optional[str],
        mtime: Optional[float],
        progress: ProgressCallback,
    ) -> DownloadResult:
        part_fs = fs_path(part)
        state_fs = part_fs + STATE_SUFFIX
        if size and (
            os.path.exists(state_fs)
            or (self.connections > 1 and size > 2 * config.min_segment_size)
        ):
            try:
                return self._download_segmented(
                    url, dest, part_fs, state_fs, size, sha256, md5, mtime, progress
                )
            except RangeNotSupported:
                logger.debug("Server ignores Range for %s, downloading in one stream", dest)
                for path in (part_fs, state_fs):
                    if os.path.exists(path):
                        os.remove(path)
        elif os.path.exists(state_fs):
            os.remove(state_fs)

        existing = os.path.getsize(part_fs) if os.path.exists(part_fs) else 0
        if size is not None and existing > size:
            os.remove(part_fs)
            existing = 0

        hasher, expected = new_hasher(sha256, md5)
        if existing and hasher is not None:
            with open(part_fs, "rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    self.client.check_cancel()
                    hasher.update(block)

        resumed = existing > 0
        received = 0
        if existing:
            progress(existing)
        if size == 0 and not existing:
            os.makedirs(os.path.dirname(part_fs) or ".", exist_ok=True)
            open(part_fs, "wb").close()
        elif size is None or existing < size:
            received, restarted, hasher = self._fetch(
                url, part_fs, existing, size, hasher, sha256, md5, progress
            )
            if restarted:
                resumed = False

        actual = os.path.getsize(part_fs)
        if size is not None and actual != size:
            if actual < size:
                raise IncompleteDownload(f"got {actual} of {size} bytes")
            os.remove(part_fs)
            raise VerificationError(f"size mismatch: got {actual}, expected {size} bytes")
        verified = False
        if hasher is not None:
            if hasher.hexdigest() != expected:
                os.remove(part_fs)
                raise VerificationError("checksum mismatch")
            verified = True

        os.replace(part_fs, fs_path(dest))
        set_mtime(dest, mtime)
        logger.debug("Downloaded %s (%s bytes, resumed=%s)", dest, actual, resumed)
        return DownloadResult(dest, actual, received, resumed, verified)

    # ------------------------------------------------------------------ segments

    @staticmethod
    def _load_state(state_fs: str, part_fs: str, size: int) -> Optional[List[List[int]]]:
        try:
            with open(state_fs, "r", encoding="utf8") as f:
                data = json.load(f)
            segments = [[int(a), int(b), int(c)] for a, b, c in data["segments"]]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if data.get("size") != size or not os.path.exists(part_fs):
            return None
        if os.path.getsize(part_fs) != size:
            return None
        if any(not (0 <= s <= s + d <= e <= size) for s, e, d in segments):
            return None
        return segments

    def _segment_size(self, remaining: int) -> int:
        """About two segments per connection, within the configured bounds."""
        per_connection = -(-remaining // (self.connections * 2))
        return max(config.min_segment_size, min(config.segment_size, per_connection))

    @staticmethod
    def _save_state(state_fs: str, size: int, segments: List[List[int]]) -> None:
        tmp = state_fs + ".tmp"
        with open(tmp, "w", encoding="utf8") as f:
            json.dump({"size": size, "segments": segments}, f)
        os.replace(tmp, state_fs)

    def _download_segmented(
        self,
        url: str,
        dest: str,
        part_fs: str,
        state_fs: str,
        size: int,
        sha256: Optional[str],
        md5: Optional[str],
        mtime: Optional[float],
        progress: ProgressCallback,
    ) -> DownloadResult:
        """Download a big file as segments fetched by several connections in parallel.

        The part file is allocated at full size; ``<part>.state`` records how much of every
        segment has been written, so an interrupted download continues where it stopped.
        """
        segments = self._load_state(state_fs, part_fs, size)
        if segments is None:
            # Continue a partial file from a single-stream download, if there is one.
            existing = os.path.getsize(part_fs) if os.path.exists(part_fs) else 0
            if existing > size:
                existing = 0
            segments = [[0, existing, existing]] if existing else []
            step = self._segment_size(size - existing)
            segments += [[s, min(s + step, size), 0] for s in range(existing, size, step)]
            os.makedirs(os.path.dirname(part_fs) or ".", exist_ok=True)
            with open(part_fs, "r+b" if existing else "wb") as f:
                f.truncate(size)
            self._save_state(state_fs, size, segments)

        done_before = sum(seg[2] for seg in segments)
        if done_before:
            progress(done_before)
        pending = [seg for seg in segments if seg[0] + seg[2] < seg[1]]
        lock = threading.Lock()
        stop = threading.Event()
        errors: List[BaseException] = []
        received = [0]
        last_save = [time.monotonic()]

        def save(force: bool = False) -> None:
            with lock:
                if force or time.monotonic() - last_save[0] > 2:
                    self._save_state(state_fs, size, segments)
                    last_save[0] = time.monotonic()

        def fetch(f, seg: List[int]) -> None:
            pos = seg[0] + seg[2]
            headers = {"Range": f"bytes={pos}-{seg[1] - 1}"}
            resp = self.client.session.get(
                url, headers=headers, stream=True, timeout=config.timeout
            )
            with closing(resp):
                if resp.status_code == 429:
                    self.client.handle_rate_limit(resp)
                    raise HttpStatusError(429)
                if resp.status_code >= 400:
                    raise HttpStatusError(resp.status_code)
                if resp.status_code != 206:
                    raise RangeNotSupported("server ignores Range requests")
                f.seek(pos)
                for chunk in resp.iter_content(chunk_size=config.chunk_size):
                    if stop.is_set():
                        return
                    self.client.check_cancel()
                    chunk = chunk[: seg[1] - pos]
                    if not chunk:
                        continue
                    f.write(chunk)
                    pos += len(chunk)
                    with lock:
                        seg[2] = pos - seg[0]
                        received[0] += len(chunk)
                    progress(len(chunk))
                    save()
            if pos < seg[1]:
                raise IncompleteDownload(f"segment ended at byte {pos} of {seg[1]}")

        def worker() -> None:
            try:
                # Unbuffered: what the state file says is written must be in the OS already.
                with open(part_fs, "r+b", buffering=0) as f:
                    while not stop.is_set():
                        with lock:
                            if not pending:
                                return
                            seg = pending.pop(0)
                        fetch(f, seg)
            except BaseException as e:
                stop.set()
                with lock:
                    errors.append(e)

        workers = min(self.connections, len(pending))
        if workers:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="segment") as ex:
                for _ in range(workers):
                    ex.submit(worker)
        save(force=True)
        if errors:
            for error in errors:
                if isinstance(error, Cancelled):
                    raise error
            for error in errors:
                if isinstance(error, RangeNotSupported):
                    progress(-(done_before + received[0]))
                    raise error
            raise errors[0]

        verified = False
        hasher, expected = new_hasher(sha256, md5)
        if hasher is not None:
            with open(part_fs, "rb") as f:
                for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
                    self.client.check_cancel()
                    hasher.update(block)
            if hasher.hexdigest() != expected:
                os.remove(part_fs)
                os.remove(state_fs)
                raise VerificationError("checksum mismatch")
            verified = True

        os.replace(part_fs, fs_path(dest))
        os.remove(state_fs)
        set_mtime(dest, mtime)
        logger.debug("Downloaded %s in segments (%s bytes)", dest, size)
        return DownloadResult(dest, size, received[0], done_before > 0, verified)

    # ------------------------------------------------------------------ single stream

    def _fetch(self, url, part_fs, existing, size, hasher, sha256, md5, progress):
        """Stream the (rest of the) file into ``part_fs``.

        Returns ``(received_bytes, restarted_from_zero, hasher)``.
        """
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        resp = self.client.session.get(url, headers=headers, stream=True, timeout=config.timeout)
        with closing(resp):
            if resp.status_code == 429:
                self.client.handle_rate_limit(resp)
                raise HttpStatusError(429)
            if resp.status_code == 416:
                # Range not satisfiable: the partial file is unusable.
                os.remove(part_fs)
                raise IncompleteDownload("server rejected resume request")
            if resp.status_code >= 400:
                raise HttpStatusError(resp.status_code)

            restarted = False
            if existing and resp.status_code != 206:
                # The server ignored the Range header and sends the whole file.
                progress(-existing)
                existing = 0
                restarted = True
                hasher, _ = new_hasher(sha256, md5)

            expected_length = resp.headers.get("Content-Length")
            received = 0
            os.makedirs(os.path.dirname(part_fs) or ".", exist_ok=True)
            with open(part_fs, "ab" if existing else "wb") as f:
                for chunk in resp.iter_content(chunk_size=config.chunk_size):
                    self.client.check_cancel()
                    if not chunk:
                        continue
                    f.write(chunk)
                    if hasher is not None:
                        hasher.update(chunk)
                    received += len(chunk)
                    progress(len(chunk))
            if size is None and expected_length and expected_length.isdigit():
                if received < int(expected_length):
                    raise IncompleteDownload(f"got {received} of {expected_length} bytes")
        return received, restarted, hasher

    def get_file(
        self,
        url: str,
        filepath: Optional[str] = None,
        filename: Optional[str] = None,
        filesize: Optional[int] = None,
        sha256: Optional[str] = None,
        md5: Optional[str] = None,
        mtime: Optional[float] = None,
        on_progress: Optional[ProgressCallback] = None,
        refresh_url: Optional[Callable[[], str]] = None,
    ) -> DownloadResult:
        """Download a file into ``filepath`` (the name is taken from the server if omitted)."""
        if filename is None:
            with closing(self.client.session.get(url, stream=True, timeout=config.timeout)) as resp:
                filename = _filename_from_headers(resp) or unquote(
                    url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
                )
        dest = os.path.join(filepath, filename) if filepath else filename
        if filepath:
            os.makedirs(fs_path(filepath), exist_ok=True)
        return self.download(
            url,
            dest,
            size=filesize,
            sha256=sha256,
            md5=md5,
            mtime=mtime,
            refresh_url=refresh_url,
            on_progress=on_progress,
        )
