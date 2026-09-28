"""A tiny local imitation of the public Yandex.Disk API used by integration tests."""

import hashlib
import json
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.parse import parse_qs, quote, urlparse

MODIFIED = "2020-05-17T10:20:30+00:00"
MODIFIED_TS = 1589710830.0


class FakeYandexDisk:
    """Serves a folder tree given as ``{"/path/to/file": b"content", "/empty/dir/": None}``."""

    def __init__(self, files: Dict[str, Optional[bytes]]):
        self.files: Dict[str, bytes] = {}
        self.dirs = {"": set()}
        for path, content in files.items():
            if path.endswith("/"):
                self._add_dir(path.rstrip("/"))
            else:
                self._add_dir(path.rsplit("/", 1)[0])
                self.files[path] = content or b""
                self.dirs[path.rsplit("/", 1)[0]].add(path)
        # Failure injection, keyed by file path.
        self.fail_download: Dict[str, int] = defaultdict(int)  # respond 500 N times
        self.truncate_once: set = set()  # send half of the file, then drop the connection
        self.corrupt_once: set = set()  # send wrong bytes once
        self.stall_once: set = set()  # send half of the file, then go silent (dead connection)
        self.stop_event = threading.Event()
        self.fail_listing: Dict[str, int] = defaultdict(int)  # folder listing 500 N times
        self.wrong_sha: set = set()  # API reports a wrong checksum (always)
        self.ignore_range = False
        self.requests = []
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _add_dir(self, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        current = ""
        for part in parts:
            parent = current
            current = f"{current}/{part}"
            self.dirs.setdefault(parent, set()).add(current + "/")
            self.dirs.setdefault(current, set())

    def __enter__(self) -> "FakeYandexDisk":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop_event.set()
        self.server.shutdown()
        self.server.server_close()

    # ------------------------------------------------------------------ resources

    def file_meta(self, path: str) -> dict:
        content = self.files[path]
        sha = hashlib.sha256(content).hexdigest()
        if path in self.wrong_sha:
            sha = "0" * 64
        return {
            "name": path.rsplit("/", 1)[-1],
            "path": path,
            "type": "file",
            "size": len(content),
            "sha256": sha,
            "md5": hashlib.md5(content).hexdigest(),
            "modified": MODIFIED,
            "file": f"{self.base}/dl?path={quote(path)}",
        }

    def dir_meta(self, path: str, limit: int, offset: int) -> dict:
        children = sorted(self.dirs[path])
        items = []
        for child in children[offset : offset + limit]:
            if child.endswith("/"):
                child = child.rstrip("/")
                items.append(
                    {
                        "name": child.rsplit("/", 1)[-1],
                        "path": child,
                        "type": "dir",
                        "modified": MODIFIED,
                    }
                )
            else:
                items.append(self.file_meta(child))
        return {
            "name": path.rsplit("/", 1)[-1] if path else "shared",
            "path": path or "/",
            "type": "dir",
            "modified": MODIFIED,
            "_embedded": {
                "items": items,
                "limit": limit,
                "offset": offset,
                "total": len(children),
                "path": path or "/",
            },
        }

    def _handler(self):
        disk = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _json(self, code: int, data: dict) -> None:
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                url = urlparse(self.path)
                qs = {k: v[0] for k, v in parse_qs(url.query).items()}
                with disk.lock:
                    disk.requests.append((url.path, qs, self.headers.get("Range")))
                if url.path == "/v1/disk/public/resources":
                    path = qs.get("path", "").rstrip("/")
                    if path == "/":
                        path = ""
                    with disk.lock:
                        if disk.fail_listing[path] > 0:
                            disk.fail_listing[path] -= 1
                            return self._json(500, {"error": "InternalError"})
                    if path in disk.files:
                        return self._json(200, disk.file_meta(path))
                    if path not in disk.dirs:
                        return self._json(404, {"error": "DiskNotFoundError"})
                    limit = int(qs.get("limit", 20))
                    offset = int(qs.get("offset", 0))
                    return self._json(200, disk.dir_meta(path, limit, offset))
                if url.path == "/v1/disk/public/resources/download":
                    path = qs.get("path", "")
                    if path and path not in disk.files and path.rstrip("/") not in disk.dirs:
                        return self._json(404, {"error": "DiskNotFoundError"})
                    return self._json(200, {"href": f"{disk.base}/dl?path={quote(path)}"})
                if url.path == "/dl":
                    return self._download(qs.get("path", ""))
                self._json(404, {"error": "not found"})

            def _download(self, path: str) -> None:
                if path.rstrip("/") in disk.dirs:
                    body = b"PK\x03\x04 fake zip of " + path.encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path not in disk.files:
                    return self._json(404, {"error": "DiskNotFoundError"})
                with disk.lock:
                    if disk.fail_download[path] > 0:
                        disk.fail_download[path] -= 1
                        return self._json(500, {"error": "InternalError"})
                    truncate = path in disk.truncate_once
                    disk.truncate_once.discard(path)
                    corrupt = path in disk.corrupt_once
                    disk.corrupt_once.discard(path)
                    stall = path in disk.stall_once
                    disk.stall_once.discard(path)
                content = disk.files[path]
                if corrupt:
                    content = bytes(len(content))
                start = 0
                range_header = self.headers.get("Range")
                if range_header and not disk.ignore_range:
                    start = int(range_header.split("=")[1].split("-")[0])
                    self.send_response(206)
                    self.send_header(
                        "Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}"
                    )
                else:
                    self.send_response(200)
                body = content[start:]
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                if stall:
                    self.wfile.write(body[: len(body) // 2])
                    self.wfile.flush()
                    disk.stop_event.wait(30)
                    self.close_connection = True
                    return
                if truncate:
                    self.wfile.write(body[: len(body) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(body)

        return Handler
