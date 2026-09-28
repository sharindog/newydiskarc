import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import yaml

from ydiskarc.client import YandexDiskClient, parse_public_url
from ydiskarc.downloader import ResourceDownloader
from ydiskarc.progress import FileTask, Reporter, SyncStats
from ydiskarc.sync import METADATA_FILENAME, SyncEngine
from ydiskarc.utils import (
    Cancelled,
    file_checksum_matches,
    fs_path,
    mtime_matches,
    parse_timestamp,
    sanitize_filename,
    set_mtime,
)

logger = logging.getLogger(__name__)


def yd_get_full(
    url: str,
    output: Optional[str],
    filename: Optional[str],
    metadata: bool,
    verbose: bool = False,
    reporter: Optional[Reporter] = None,
    cancel_event: Optional[threading.Event] = None,
    safe_names: Optional[bool] = None,
) -> SyncStats:
    """Download a public file, or a public folder as a ZIP archive."""
    parsed = parse_public_url(url)
    if parsed is None:
        raise ValueError(f"Invalid Yandex.Disk URL: {url}")
    reporter = reporter or Reporter()
    client = YandexDiskClient(verbose=verbose, cancel_event=cancel_event)
    downloader = ResourceDownloader(client, verbose=verbose)
    stats = SyncStats(started=time.time())
    path = parsed.path or None
    if output is None:
        output = parsed.key

    try:
        data = client.get_resource_metadata(parsed.public_key, path=path, limit=1).json()
        os.makedirs(fs_path(output), exist_ok=True)
        if metadata:
            with open(fs_path(os.path.join(output, METADATA_FILENAME)), "w", encoding="utf8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            stats.metadata_files = 1

        is_dir = data.get("type") == "dir"
        if filename is None:
            filename = "dump.zip" if is_dir else data.get("name") or parsed.key
            filename = sanitize_filename(filename, safe_names)
        size = None if is_dir else data.get("size")
        task = FileTask(
            remote_path=data.get("path") or "/",
            local_path=os.path.join(output, filename),
            display=filename,
            size=size,
            sha256=None if is_dir else data.get("sha256"),
            md5=None if is_dir else data.get("md5"),
            modified=parse_timestamp(data.get("modified")),
        )
        stats.dirs = 1 if is_dir else 0
        stats.total_files = 1
        stats.total_bytes = size or 0

        local = fs_path(task.local_path)
        if not is_dir and os.path.exists(local) and os.path.getsize(local) == size:
            if mtime_matches(local, task.modified) or file_checksum_matches(
                local, task.sha256, task.md5, client.cancel_event
            ) in (True, None):
                set_mtime(task.local_path, task.modified)
                stats.skipped_files = 1
                reporter.start(stats, 0, 0, 0)
                reporter.file_end(task, "skipped")
                return stats

        stats.todo_files = 1
        stats.todo_bytes = size or 0
        reporter.start(stats, 0, 1, size or 0)
        reporter.file_start(task)
        try:
            result = downloader.download(
                None,
                task.local_path,
                size=task.size,
                sha256=task.sha256,
                md5=task.md5,
                mtime=task.modified,
                refresh_url=lambda: client.get_download_link(parsed.public_key, path),
                on_progress=lambda n: reporter.file_progress(task, n),
            )
        except Exception as e:
            stats.failed_files[task.display] = str(e)
            reporter.file_end(task, "failed", str(e))
        else:
            stats.downloaded_files = 1
            stats.downloaded_bytes = result.size
            stats.received_bytes = result.downloaded
            stats.resumed_files = int(result.resumed)
            stats.verified_files = int(result.verified)
            reporter.file_end(task, "downloaded")
    except (Cancelled, KeyboardInterrupt):
        stats.cancelled = True
    except Exception as e:
        logger.debug("Full download failed", exc_info=True)
        stats.error = str(e) or e.__class__.__name__
    finally:
        stats.finished = time.time()
        reporter.finish(stats)
    return stats


class Project:
    """Disk files extractor. Yandex.Disk only right now"""

    def __init__(self) -> None:
        pass

    def configure(self, key: str, projectdir: Optional[str] = None) -> None:
        if projectdir is None:
            projectdir = os.getcwd()
        filepath = os.path.join(projectdir, ".ydiskarc")

        conf: Dict[str, Any] = {}
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf8") as f:
                conf = yaml.safe_load(f) or {}

        if "keys" not in conf:
            conf["keys"] = {}
        conf["keys"]["yandex_oauth"] = key

        with open(filepath, "w", encoding="utf8") as f:
            yaml.safe_dump(conf, f)
        logger.info("Configuration saved at %s", filepath)

    def sync(
        self,
        url: str,
        output: str,
        nofiles: bool = False,
        verbose: bool = False,
        threads: Optional[int] = None,
        flat: bool = False,
        safe_names: Optional[bool] = None,
        verify: bool = False,
        retries: Optional[int] = None,
        reporter: Optional[Reporter] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> SyncStats:
        """Synchronize a public folder. Existing up-to-date files are always skipped."""
        engine = SyncEngine(
            url,
            output,
            nofiles=nofiles,
            threads=threads,
            flat=flat,
            safe_names=safe_names,
            verify=verify,
            retries=retries,
            reporter=reporter,
            cancel_event=cancel_event,
            verbose=verbose,
        )
        return engine.run()

    def full(
        self,
        url: str,
        output: Optional[str],
        filename: Optional[str],
        metadata: bool,
        verbose: bool = False,
        reporter: Optional[Reporter] = None,
        safe_names: Optional[bool] = None,
    ) -> SyncStats:
        return yd_get_full(
            url, output, filename, metadata, verbose, reporter=reporter, safe_names=safe_names
        )
