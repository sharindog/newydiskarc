"""Synchronization of a public Yandex.Disk folder to a local directory."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .client import ApiError, PublicUrl, YandexDiskClient, parse_public_url
from .config import config
from .downloader import ResourceDownloader
from .i18n import tr
from .progress import FileTask, Reporter, SyncStats
from .utils import (
    Cancelled,
    _short_hash,
    default_safe_names,
    file_checksum_matches,
    fs_path,
    mtime_matches,
    parse_timestamp,
    sanitize_filename,
    set_mtime,
)

logger = logging.getLogger(__name__)

METADATA_FILENAME = "_metadata.json"


@dataclass
class DirJob:
    remote_path: str
    local_dir: str
    data: Optional[Dict[str, Any]] = None


def _with_suffix(name: str, remote_path: str) -> str:
    stem, ext = os.path.splitext(name)
    return f"{stem}~{_short_hash(remote_path)}{ext}"


def _items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    embedded = data.get("_embedded")
    if not isinstance(embedded, dict):
        return []
    return [row for row in embedded.get("items") or [] if isinstance(row, dict)]


class SyncEngine:
    """Mirror a public folder (or a subfolder of it) into ``output``.

    Files that already exist locally with the right size (and date, or checksum) are skipped;
    everything else is downloaded, verified and gets its original modification time.
    Folders and files that fail are retried in extra passes; the rest are reported at the end.
    """

    def __init__(
        self,
        url: str,
        output: str,
        nofiles: bool = False,
        threads: Optional[int] = None,
        connections: Optional[int] = None,
        flat: bool = False,
        safe_names: Optional[bool] = None,
        verify: bool = False,
        retries: Optional[int] = None,
        reporter: Optional[Reporter] = None,
        cancel_event: Optional[threading.Event] = None,
        verbose: bool = False,
        retry_pause: Optional[float] = None,
    ):
        parsed = parse_public_url(url)
        if parsed is None:
            raise ValueError(f"Invalid Yandex.Disk URL: {url}")
        self.url: PublicUrl = parsed
        self.output = output
        self.nofiles = nofiles
        self.threads = max(1, threads or config.threads)
        self.flat = flat
        self.safe_names = default_safe_names() if safe_names is None else safe_names
        self.verify = verify
        self.retries = config.retry_rounds if retries is None else max(0, retries)
        self.retry_pause = config.retry_round_pause if retry_pause is None else retry_pause
        self.reporter = reporter or Reporter()
        self.cancel_event = cancel_event or threading.Event()
        self.client = YandexDiskClient(verbose=verbose, cancel_event=self.cancel_event)
        self.downloader = ResourceDownloader(self.client, verbose=verbose, connections=connections)
        self.stats = SyncStats()
        self._lock = threading.Lock()
        self._failed_tasks: Dict[str, FileTask] = {}
        self._failed_dirs: List[DirJob] = []
        self._dir_times: List[Tuple[str, float]] = []
        self._flat_names: Dict[str, str] = {}
        self._base = self.url.path

    # ------------------------------------------------------------------ helpers

    def cancel(self) -> None:
        self.cancel_event.set()

    def _display(self, remote_path: str) -> str:
        if self._base and remote_path.startswith(self._base):
            remote_path = remote_path[len(self._base) :]
        return remote_path.lstrip("/") or remote_path

    def _fetch_root(self) -> Dict[str, Any]:
        path = self.url.path or None
        try:
            return self.client.get_listing(self.url.public_key, path)
        except ApiError as e:
            # A folder whose name really contains "%xx" pasted as a "pretty" link.
            if e.status == 404 and self.url.raw_path and self.url.raw_path != self.url.path:
                data = self.client.get_listing(self.url.public_key, self.url.raw_path)
                self._base = self.url.raw_path
                return data
            raise

    def _write_metadata(self, local_dir: str, data: Dict[str, Any]) -> None:
        os.makedirs(fs_path(local_dir), exist_ok=True)
        path = os.path.join(local_dir, METADATA_FILENAME)
        with open(fs_path(path), "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.stats.metadata_files += 1

    def _make_task(self, row: Dict[str, Any], local_path: str) -> FileTask:
        size = row.get("size")
        task = FileTask(
            remote_path=row.get("path") or "/" + row.get("name", ""),
            local_path=local_path,
            display=self._display(row.get("path") or row.get("name", "")),
            size=size if isinstance(size, int) else None,
            sha256=row.get("sha256"),
            md5=row.get("md5"),
            modified=parse_timestamp(row.get("modified")),
            href=row.get("file"),
        )
        try:
            task.present = (
                task.size is not None and os.path.getsize(fs_path(local_path)) == task.size
            )
        except OSError:
            task.present = False
        return task

    # ------------------------------------------------------------------ scanning

    def _scan(self, jobs: List[DirJob]) -> List[FileTask]:
        """Walk folders breadth-first, returning the files found."""
        tasks: List[FileTask] = []
        queue = deque(jobs)
        while queue:
            self.client.check_cancel()
            job = queue.popleft()
            data = job.data
            if data is None:
                try:
                    data = self.client.get_listing(self.url.public_key, job.remote_path)
                except Cancelled:
                    raise
                except Exception as e:
                    reason = str(e)
                    self._failed_dirs.append(job)
                    self.stats.failed_dirs[self._display(job.remote_path) or "/"] = reason
                    self.reporter.message(
                        "warning", tr("failed_folder", path=job.remote_path, reason=reason)
                    )
                    continue
            self.stats.failed_dirs.pop(self._display(job.remote_path) or "/", None)
            self.stats.dirs += 1
            if not self.flat or self.nofiles:
                os.makedirs(fs_path(job.local_dir), exist_ok=True)
            if self.nofiles:
                self._write_metadata(job.local_dir, data)
            modified = parse_timestamp(data.get("modified"))
            if modified is not None and (not self.flat or self.nofiles):
                self._dir_times.append((job.local_dir, modified))

            used: Dict[str, str] = {}
            for row in _items(data):
                name = row.get("name")
                if not name:
                    continue
                remote_path = row.get("path") or f"{job.remote_path.rstrip('/')}/{name}"
                local_name = sanitize_filename(name, self.safe_names)
                # Names that differ only by case (or became equal after sanitizing) would
                # overwrite each other on Windows.
                if local_name.lower() in used:
                    local_name = _with_suffix(local_name, remote_path)
                used[local_name.lower()] = remote_path
                if row.get("type") == "dir":
                    queue.append(DirJob(remote_path, os.path.join(job.local_dir, local_name)))
                    continue
                if row.get("type") != "file" or self.nofiles:
                    if row.get("type") == "file":
                        self.stats.total_files += 1
                        self.stats.total_bytes += row.get("size") or 0
                    continue
                if self.flat:
                    key = local_name.lower()
                    if key in self._flat_names and self._flat_names[key] != remote_path:
                        local_name = _with_suffix(local_name, remote_path)
                        key = local_name.lower()
                    self._flat_names[key] = remote_path
                    local_path = os.path.join(self.output, local_name)
                else:
                    local_path = os.path.join(job.local_dir, local_name)
                task = self._make_task(row, local_path)
                tasks.append(task)
                self.stats.total_files += 1
                self.stats.total_bytes += task.size or 0
            self.reporter.scan_progress(
                self.stats.dirs, self.stats.total_files, self.stats.total_bytes
            )
        return tasks

    # ------------------------------------------------------------------ downloading

    def _is_up_to_date(self, task: FileTask) -> bool:
        local = fs_path(task.local_path)
        try:
            size = os.path.getsize(local)
        except OSError:
            return False
        if task.size is not None and size != task.size:
            return False
        if not self.verify and (task.modified is None or mtime_matches(local, task.modified)):
            return True
        self.reporter.file_start(task, "verify")
        matches = file_checksum_matches(local, task.sha256, task.md5, self.cancel_event)
        if matches is False:
            return False
        if matches:
            with self._lock:
                self.stats.verified_files += 1
        set_mtime(task.local_path, task.modified)
        return True

    def _process(self, task: FileTask) -> None:
        if self.cancel_event.is_set():
            return
        try:
            if os.path.exists(fs_path(task.local_path)):
                if self._is_up_to_date(task):
                    with self._lock:
                        self.stats.skipped_files += 1
                        self._failed_tasks.pop(task.remote_path, None)
                        self.stats.failed_files.pop(task.display, None)
                    self.reporter.file_end(task, "skipped")
                    return
                if task.present:
                    self.reporter.add_total(task.size or 0)
                    task.present = False
            self.reporter.file_start(task, "download")

            remote = task.remote_path if task.remote_path.strip("/") else None

            def refresh() -> str:
                return self.client.get_download_link(self.url.public_key, remote)

            result = self.downloader.download(
                task.href,
                task.local_path,
                size=task.size,
                sha256=task.sha256,
                md5=task.md5,
                mtime=task.modified,
                refresh_url=refresh,
                on_progress=lambda n: self.reporter.file_progress(task, n),
            )
            task.href = None  # a link that worked once may be stale for a retry
            with self._lock:
                self.stats.downloaded_files += 1
                self.stats.downloaded_bytes += result.size
                self.stats.received_bytes += result.downloaded
                self.stats.resumed_files += int(result.resumed)
                self.stats.verified_files += int(result.verified)
                self._failed_tasks.pop(task.remote_path, None)
                self.stats.failed_files.pop(task.display, None)
            self.reporter.file_end(task, "downloaded")
        except Cancelled:
            self.reporter.file_end(task, "cancelled")
        except Exception as e:
            reason = str(e) or e.__class__.__name__
            logger.debug("Failed to download %s", task.remote_path, exc_info=True)
            task.href = None
            with self._lock:
                self._failed_tasks[task.remote_path] = task
                self.stats.failed_files[task.display] = reason
            self.reporter.file_end(task, "failed", reason)
            self.reporter.message("warning", tr("failed_file", path=task.display, reason=reason))

    def _download(self, tasks: List[FileTask], pass_no: int) -> None:
        todo = [t for t in tasks if not t.present]
        todo_bytes = sum(t.size or 0 for t in todo)
        if pass_no == 0:
            self.stats.todo_files = len(todo)
            self.stats.todo_bytes = todo_bytes
        self.reporter.start(self.stats, pass_no, len(todo), todo_bytes)
        if not tasks:
            return
        executor = ThreadPoolExecutor(max_workers=self.threads, thread_name_prefix="ydiskarc")
        try:
            futures = [executor.submit(self._process, task) for task in tasks]
            pending = set(futures)
            while pending:
                # A timeout keeps the main thread responsive to Ctrl+C.
                _, pending = wait(pending, timeout=0.5)
        except BaseException:
            self.cancel_event.set()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        self.client.check_cancel()

    # ------------------------------------------------------------------ main

    def run(self) -> SyncStats:
        stats = self.stats
        stats.started = time.time()
        try:
            root = self._fetch_root()
            if root.get("type") == "file":
                self._run_single_file(root)
            else:
                tasks = self._scan([DirJob(self._base, self.output, root)])
                if not self.nofiles:
                    self._download(tasks, 0)
            for pass_no in range(1, self.retries + 1):
                if not self._failed_tasks and not self._failed_dirs:
                    break
                self.reporter.message(
                    "info",
                    tr(
                        "retry_pass",
                        n=pass_no,
                        total=self.retries,
                        files=len(self._failed_tasks),
                        dirs=len(self._failed_dirs),
                        pause=f"{self.retry_pause:g}",
                    ),
                )
                self.client.sleep(self.retry_pause)
                dirs, self._failed_dirs = self._failed_dirs, []
                tasks = list(self._failed_tasks.values()) + self._scan(dirs)
                if not self.nofiles:
                    self._download(tasks, pass_no)
        except (Cancelled, KeyboardInterrupt):
            self.cancel_event.set()
            stats.cancelled = True
        except Exception as e:
            logger.debug("Sync failed", exc_info=True)
            stats.error = str(e) or e.__class__.__name__
        finally:
            for path, timestamp in sorted(self._dir_times, key=lambda x: -len(x[0])):
                set_mtime(path, timestamp)
            stats.finished = time.time()
            self.reporter.finish(stats)
        return stats

    def _run_single_file(self, row: Dict[str, Any]) -> None:
        self.stats.dirs = 0
        if self.nofiles:
            self._write_metadata(self.output, row)
            self.stats.total_files = 1
            self.stats.total_bytes = row.get("size") or 0
            return
        os.makedirs(fs_path(self.output), exist_ok=True)
        name = sanitize_filename(row.get("name") or self.url.key, self.safe_names)
        task = self._make_task(row, os.path.join(self.output, name))
        task.display = row.get("name") or name
        self.stats.total_files = 1
        self.stats.total_bytes = task.size or 0
        self._download([task], 0)
