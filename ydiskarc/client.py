import logging
import re
import time
from typing import Dict, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import config


class YandexDiskClient:
    """Client for interacting with Yandex.Disk API."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.api_url = config.api_resources_url
        self.api_download_url = config.api_download_url
        self.session = self._create_session_with_retries()

    def _create_session_with_retries(self) -> requests.Session:
        """Create a requests session with retry logic and rate limiting handling."""
        session = requests.Session()
        retry_strategy = Retry(
            total=config.max_retries,
            backoff_factor=config.retry_backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        headers = {"User-Agent": config.user_agent or ""}
        session.headers.update(headers)
        return session

    def handle_rate_limit(self, resp: requests.Response) -> None:
        """Handle rate limiting by waiting for the specified time."""
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            if self.verbose:
                logging.warning(f"Rate limited. Waiting {retry_after} seconds...")
            time.sleep(retry_after)

    @staticmethod
    def validate_yandex_url(url: str) -> bool:
        """Validate Yandex.Disk public resource URL."""
        patterns = [
            r"https://disk\.yandex\.ru/d/[A-Za-z0-9_-]+",
            r"https://disk\.yandex\.ru/i/[A-Za-z0-9_-]+",
            r"https://disk\.yandex\.com/d/[A-Za-z0-9_-]+",
            r"https://disk\.yandex\.com/i/[A-Za-z0-9_-]+",
        ]
        return any(re.match(pattern, url) for pattern in patterns)

    def get_resource_metadata(
        self, url: str, path: Optional[str] = None, limit: int = 1000
    ) -> requests.Response:
        """Fetch resource metadata from Yandex.Disk API."""
        params: Dict[str, Any] = {"public_key": url}
        if path is not None:
            params["path"] = path
            params["limit"] = limit

        resp = self.session.get(self.api_url, params=params, timeout=config.timeout)
        if resp.status_code == 429:
            self.handle_rate_limit(resp)
            resp = self.session.get(self.api_url, params=params, timeout=config.timeout)

        resp.raise_for_status()
        return resp

    def get_download_link(self, url: str) -> str:
        """Get the direct download link for a public resource."""
        resp = self.session.get(
            self.api_download_url, params={"public_key": url}, timeout=config.timeout
        )
        if resp.status_code == 429:
            self.handle_rate_limit(resp)
            resp = self.session.get(
                self.api_download_url, params={"public_key": url}, timeout=config.timeout
            )

        resp.raise_for_status()
        data = resp.json()
        if "href" not in data:
            raise ValueError(f"No download URL found for {url}")

        return data["href"]
