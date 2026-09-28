"""Filesystem and formatting helpers shared by the CLI and the GUI."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from typing import Optional

# Characters that are forbidden in file names on Windows (and on FAT/exFAT/NTFS volumes
# mounted anywhere). They are replaced with visually similar full-width characters so that
# names stay readable and distinct: ``"a?b"`` becomes ``"a？b"``.
_WINDOWS_REPLACEMENTS = {
    '"': "＂",
    "<": "＜",
    ">": "＞",
    ":": "：",
    "|": "｜",
    "?": "？",
    "*": "＊",
    "\\": "＼",
}
_WINDOWS_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..*)?$", re.IGNORECASE)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")

# Room left in a file name for the temporary download suffix.
_SUFFIX_RESERVE = 16
_MAX_NAME_CHARS = 255 - _SUFFIX_RESERVE  # NTFS counts UTF-16 units
_MAX_NAME_BYTES = 255 - _SUFFIX_RESERVE  # ext4 and most POSIX filesystems count bytes


class Cancelled(Exception):
    """Raised when the user stops the download."""


def default_safe_names() -> bool:
    """Whether Windows-safe file names should be used by default on this platform."""
    return os.name == "nt"


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]


def _truncate_name(name: str, safe_names: bool) -> str:
    """Shorten a too long name, keeping its extension and adding a hash to keep it unique."""

    def too_long(value: str) -> bool:
        if safe_names or os.name == "nt":
            return len(value) > _MAX_NAME_CHARS
        return len(value.encode("utf-8", "surrogatepass")) > _MAX_NAME_BYTES

    if not too_long(name):
        return name
    stem, ext = os.path.splitext(name)
    if len(ext) > 32:
        stem, ext = name, ""
    tail = "~" + _short_hash(name) + ext
    while stem and too_long(stem + tail):
        stem = stem[:-1]
    return stem + tail


def sanitize_filename(name: str, safe_names: Optional[bool] = None) -> str:
    """Turn a remote file or folder name into a name that can be stored locally.

    Names are never URL-decoded: a file called ``a%3Fb`` is stored as ``a%3Fb``.
    """
    if safe_names is None:
        safe_names = default_safe_names()
    result = name.replace("/", "∕").replace("\x00", "")
    if safe_names:
        result = "".join(_WINDOWS_REPLACEMENTS.get(ch, ch) for ch in result)
        result = _CONTROL_CHARS.sub("_", result)
        # Windows silently drops trailing dots and spaces.
        stripped = result.rstrip(". ")
        if stripped != result:
            result = stripped + "_" * (len(result) - len(stripped))
        if _WINDOWS_RESERVED.match(result):
            result = "_" + result
    if result in ("", ".", ".."):
        result = result.replace(".", "_") or "_"
    return _truncate_name(result, safe_names)


def fs_path(path: str) -> str:
    """Return a path usable for file operations.

    On Windows long paths (over MAX_PATH) need the ``\\\\?\\`` prefix to be accessible.
    """
    if os.name != "nt":
        return path
    if path.startswith("\\\\?\\"):
        return path
    absolute = os.path.abspath(path)
    if len(absolute) < 240:
        return path
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def format_size(size_bytes: Optional[float]) -> str:
    """Format size in bytes to human-readable format."""
    if size_bytes is None:
        return "?"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def format_duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_timestamp(value: Optional[str]) -> Optional[float]:
    """Parse an ISO 8601 timestamp from the API (``2021-01-01T10:00:00+00:00``)."""
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def set_mtime(path: str, timestamp: Optional[float]) -> None:
    """Set modification (and access) time of a file or directory, ignoring failures."""
    if timestamp is None:
        return
    try:
        os.utime(fs_path(path), (timestamp, timestamp))
    except (OSError, OverflowError, ValueError):
        pass


def mtime_matches(path: str, timestamp: Optional[float]) -> bool:
    """Whether the file modification time equals ``timestamp`` (FAT has 2 s resolution)."""
    if timestamp is None:
        return False
    try:
        return abs(os.path.getmtime(fs_path(path)) - timestamp) <= 2
    except OSError:
        return False


def new_hasher(sha256: Optional[str], md5: Optional[str]):
    """Create a hash object for the strongest checksum the API reported."""
    if sha256:
        return hashlib.sha256(), sha256.lower()
    if md5:
        return hashlib.md5(), md5.lower()
    return None, None


def file_checksum_matches(
    path: str, sha256: Optional[str], md5: Optional[str], cancel=None
) -> Optional[bool]:
    """Check the checksum of a local file. Returns ``None`` when no checksum is known."""
    hasher, expected = new_hasher(sha256, md5)
    if hasher is None:
        return None
    with open(fs_path(path), "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            block = f.read(1024 * 1024)
            if not block:
                break
            hasher.update(block)
    return bool(hasher.hexdigest() == expected)
