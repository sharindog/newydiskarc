"""Progress reporting: a small interface implemented by the console (rich) and the GUI."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from .i18n import tr
from .utils import format_duration, format_size

if TYPE_CHECKING:  # pragma: no cover
    from rich.console import Console


@dataclass
class FileTask:
    """One file to download."""

    remote_path: str
    local_path: str
    display: str
    size: Optional[int] = None
    sha256: Optional[str] = None
    md5: Optional[str] = None
    modified: Optional[float] = None
    href: Optional[str] = None
    present: bool = False  # a local file of the same size already exists


@dataclass
class SyncStats:
    total_files: int = 0
    total_bytes: int = 0
    dirs: int = 0
    todo_files: int = 0
    todo_bytes: int = 0
    downloaded_files: int = 0
    downloaded_bytes: int = 0
    received_bytes: int = 0
    resumed_files: int = 0
    verified_files: int = 0
    skipped_files: int = 0
    metadata_files: int = 0
    failed_files: Dict[str, str] = field(default_factory=dict)
    failed_dirs: Dict[str, str] = field(default_factory=dict)
    cancelled: bool = False
    error: Optional[str] = None
    started: float = 0.0
    finished: float = 0.0

    @property
    def elapsed(self) -> float:
        return max(self.finished - self.started, 0.0)

    @property
    def ok(self) -> bool:
        return (
            not self.failed_files and not self.failed_dirs and not self.cancelled and not self.error
        )


def format_summary(stats: SyncStats, nofiles: bool = False) -> List[Tuple[str, str]]:
    """Human readable summary as ``(level, line)`` pairs; level is info/warning/error."""
    lines: List[Tuple[str, str]] = []
    add = lines.append
    if stats.cancelled:
        add(("warning", tr("stopped")))
    if stats.error:
        add(("error", tr("error", error=stats.error)))
    size = format_size(stats.total_bytes)
    add(("info", tr("totals", dirs=stats.dirs, files=stats.total_files, size=size)))
    if nofiles:
        add(("info", tr("metadata_written", count=stats.metadata_files)))
    else:
        speed = stats.received_bytes / stats.elapsed if stats.elapsed > 0 else 0
        line = tr(
            "downloaded", count=stats.downloaded_files, size=format_size(stats.downloaded_bytes)
        )
        if stats.resumed_files:
            line += tr("resumed", count=stats.resumed_files)
        line += tr("verified", count=stats.verified_files)
        add(("info", line))
        add(("info", tr("up_to_date", count=stats.skipped_files)))
        add(("info", tr("time", time=format_duration(stats.elapsed), speed=format_size(speed))))
    if stats.failed_dirs:
        add(("error", tr("failed_folders", count=len(stats.failed_dirs))))
        for path, reason in sorted(stats.failed_dirs.items()):
            add(("error", f"  {path or '/'}: {reason}"))
    if stats.failed_files:
        add(("error", tr("failed_files", count=len(stats.failed_files))))
        for path, reason in sorted(stats.failed_files.items()):
            add(("error", f"  {path}: {reason}"))
    if stats.ok:
        add(("info", tr("done_meta" if nofiles else "done")))
    return lines


class Reporter:
    """Receives progress events. Methods may be called from several threads."""

    def scan_progress(self, dirs: int, files: int, size: int) -> None:
        pass

    def start(self, stats: SyncStats, pass_no: int, files: int, size: int) -> None:
        """Download pass ``pass_no`` (0 = main, >0 = retry) of ``files`` files starts."""

    def add_total(self, size: int) -> None:
        """A file that looked up to date has to be downloaded after all."""

    def file_start(self, task: FileTask, action: str = "download") -> None:
        """``action`` is ``download`` or ``verify`` (checksum of an existing file)."""

    def file_progress(self, task: FileTask, nbytes: int) -> None:
        pass

    def file_end(self, task: FileTask, status: str, error: Optional[str] = None) -> None:
        """``status`` is ``downloaded``, ``skipped``, ``failed`` or ``cancelled``."""

    def message(self, level: str, text: str) -> None:
        pass

    def finish(self, stats: SyncStats) -> None:
        pass


def _short(text: str, width: int = 40) -> str:
    return text if len(text) <= width else "…" + text[-(width - 1) :]


class RichReporter(Reporter):
    """Console progress: an overall bar plus one bar per file being downloaded."""

    def __init__(self, console: Optional["Console"] = None, nofiles: bool = False):
        from rich.console import Console
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            TextColumn,
            TimeRemainingColumn,
            TransferSpeedColumn,
        )

        self.console = console or Console(stderr=True)
        self.nofiles = nofiles
        self.progress = Progress(
            TextColumn("{task.description}", justify="left"),
            BarColumn(bar_width=None),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=self.console,
            expand=True,
        )
        self._lock = threading.Lock()
        self._scan_task: Optional[Any] = None
        self._overall: Optional[Any] = None
        self._files: Dict[int, Any] = {}
        self._done = 0
        self._pass_files = 0
        self._pass_no = 0
        self._overall_total = 0
        self._started = False

    def __enter__(self) -> "RichReporter":
        self.progress.start()
        self._started = True
        return self

    def __exit__(self, *exc) -> None:
        self._stop()

    def _stop(self) -> None:
        if self._started:
            self.progress.stop()
            self._started = False

    def scan_progress(self, dirs: int, files: int, size: int) -> None:
        text = f"Scanning: {dirs} folders, {files} files ({format_size(size)})"
        with self._lock:
            if self._scan_task is None:
                self._scan_task = self.progress.add_task(text, total=None)
            else:
                self.progress.update(self._scan_task, description=text)

    def start(self, stats: SyncStats, pass_no: int, files: int, size: int) -> None:
        with self._lock:
            if self._scan_task is not None:
                self.progress.remove_task(self._scan_task)
                self._scan_task = None
            if self._overall is not None:
                self.progress.remove_task(self._overall)
            if pass_no == 0:
                self.console.print(
                    f"Total: {stats.total_files} files ({format_size(stats.total_bytes)}) "
                    f"in {stats.dirs} folders; to download: {files} files ({format_size(size)})"
                )
            self._done = 0
            self._pass_files = files
            self._pass_no = pass_no
            self._overall_total = size
            self._overall = self.progress.add_task(self._overall_text(pass_no), total=size)

    def _overall_text(self, pass_no: int) -> str:
        prefix = "Total" if pass_no == 0 else f"Retry {pass_no}:"
        return f"[bold]{prefix} {self._done}/{self._pass_files}[/bold]"

    def add_total(self, size: int) -> None:
        with self._lock:
            if self._overall is not None:
                self._overall_total += size
                self.progress.update(self._overall, total=self._overall_total)

    def file_start(self, task: FileTask, action: str = "download") -> None:
        prefix = "checking " if action == "verify" else ""
        with self._lock:
            previous = self._files.pop(id(task), None)
            if previous is not None:
                self.progress.remove_task(previous)
            self._files[id(task)] = self.progress.add_task(
                prefix + _short(task.display), total=task.size if action == "download" else None
            )

    def file_progress(self, task: FileTask, nbytes: int) -> None:
        file_task = self._files.get(id(task))
        if file_task is not None:
            self.progress.advance(file_task, nbytes)
        if self._overall is not None:
            self.progress.advance(self._overall, nbytes)

    def file_end(self, task: FileTask, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            file_task = self._files.pop(id(task), None)
            if file_task is not None:
                self.progress.remove_task(file_task)
            if status in ("downloaded", "skipped", "failed"):
                self._done += 1
            if self._overall is not None:
                self.progress.update(self._overall, description=self._overall_text(self._pass_no))

    def message(self, level: str, text: str) -> None:
        style = {"warning": "yellow", "error": "bold red"}.get(level)
        self.console.print(text, style=style, markup=False, highlight=False)

    def finish(self, stats: SyncStats) -> None:
        self._stop()
        self.console.print()
        for level, line in format_summary(stats, self.nofiles):
            self.message(level, line)
