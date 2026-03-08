import logging
import os
import re
import subprocess
from typing import Dict, Optional, Any

import requests
from tqdm import tqdm

from .config import config
from .client import YandexDiskClient


def format_size(size_bytes: int) -> str:
    """Format size in bytes to human-readable format."""
    size: float = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


class ResourceDownloader:
    """Handles file downloads with tracking, resuming, and safety (.part files)."""

    def __init__(self, client: YandexDiskClient, verbose: bool = False):
        self.client = client
        self.verbose = verbose

    def get_file(
        self,
        url: str,
        filepath: Optional[str] = None,
        filename: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        aria2: bool = False,
        aria2path: Optional[str] = None,
        makedirs: bool = True,
        filesize: Optional[int] = None,
        resume: bool = True,
    ) -> None:
        """
        Download a file from a URL with progress tracking and resume support.
        Downloads are written to a `.part` extension during the process.
        """
        session = self.client.session
        headers: Dict[str, str] = {}

        try:
            page = session.get(
                url,
                params=params,
                headers=headers,
                stream=True,
                verify=True,
                timeout=config.timeout,
            )
            if page.status_code == 429:
                self.client.handle_rate_limit(page)
                page = session.get(
                    url,
                    params=params,
                    headers=headers,
                    stream=True,
                    verify=True,
                    timeout=config.timeout,
                )
            page.raise_for_status()
        except requests.exceptions.RequestException as e:
            if self.verbose:
                logging.error(f"Failed to download file from {url}: {e}")
            raise

        # Determine target filename
        if filename is None:
            if "Content-Disposition" in page.headers.keys():
                try:
                    fname = (
                        re.findall("filename=(.+)", page.headers["Content-Disposition"])[0]
                        .strip('"')
                        .encode("latin-1")
                        .decode("utf-8")
                    )
                except (IndexError, UnicodeDecodeError):
                    fname = url.split("/")[-1]
            else:
                fname = url.split("/")[-1]
            if filepath:
                filename = os.path.join(filepath, fname)
            else:
                filename = fname
        elif filepath is not None:
            filename = os.path.join(filepath, filename)

        # Temporary file for downloading
        part_filename = f"{filename}.part"

        # Check for existing file or partial file
        existing_size = 0
        if resume and os.path.exists(part_filename):
            existing_size = os.path.getsize(part_filename)
        elif resume and os.path.exists(filename):
            existing_size = os.path.getsize(filename)
            part_filename = filename  # Resume the actual file if no .part is present

        if existing_size > 0:
            page.close()
            headers["Range"] = f"bytes={existing_size}-"
            if self.verbose:
                logging.info(f"Resuming download from byte {existing_size}")

            try:
                page = session.get(
                    url,
                    params=params,
                    headers=headers,
                    stream=True,
                    verify=True,
                    timeout=config.timeout,
                )
                if page.status_code == 429:
                    self.client.handle_rate_limit(page)
                    page = session.get(
                        url,
                        params=params,
                        headers=headers,
                        stream=True,
                        verify=True,
                        timeout=config.timeout,
                    )
                page.raise_for_status()
            except requests.exceptions.RequestException as e:
                if self.verbose:
                    logging.error(f"Failed to resume download from {url}: {e}")
                raise

        # Create directory if needed
        if makedirs and filepath:
            os.makedirs(filepath, exist_ok=True)

        if not aria2:
            remaining = (
                filesize - existing_size
                if existing_size > 0 and filesize is not None
                else (filesize if filesize is not None else None)
            )
            desc = f"Downloading {os.path.basename(filename)}" + (
                " (resuming)" if existing_size > 0 else ""
            )

            if self.verbose:
                logging.info(f"Retrieving file to {part_filename}")

            try:
                mode = "ab" if existing_size > 0 and resume else "wb"
                with open(part_filename, mode) as f:
                    total = existing_size
                    with tqdm(
                        total=remaining,
                        desc=desc,
                        unit="B",
                        unit_scale=True,
                        disable=filesize is None,
                    ) as pbar:
                        for chunk in page.iter_content(chunk_size=config.chunk_size):
                            if chunk:
                                f.write(chunk)
                                total += len(chunk)
                                if filesize is not None:
                                    pbar.update(len(chunk))

                # Rename .part to requested filename upon successful completion
                if part_filename != filename:
                    os.rename(part_filename, filename)

                if self.verbose:
                    logging.info(f"Successfully downloaded {filename} ({total} bytes)")
            except Exception as e:
                if self.verbose:
                    logging.error(f"Failed to write file {filename}: {e}")
                raise
        else:
            if aria2path is None:
                raise ValueError("aria2path must be provided when using aria2")

            dirpath = os.path.dirname(filename)
            basename = os.path.basename(filename)

            try:
                cmd = [aria2path, "--retry-wait=10", "--out", basename, url]
                if len(dirpath) > 0:
                    cmd = [aria2path, "--retry-wait=10", "-d", dirpath, "--out", basename, url]

                subprocess.run(cmd, check=True, timeout=3600)
                if self.verbose:
                    logging.info(f"Successfully downloaded {filename} using aria2")
            except subprocess.CalledProcessError as e:
                if self.verbose:
                    logging.error(f"aria2 download failed: {e}")
                raise
