import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import config
from .utils import Cancelled

logger = logging.getLogger(__name__)

_PUBLIC_URL_RE = re.compile(
    r"^\s*(?:https?://)?(?:www\.)?"
    r"(?P<host>disk\.(?:360\.)?yandex\.[a-z]{2,3}(?:\.[a-z]{2})?|yadi\.sk)"
    r"/(?P<kind>[di])/(?P<key>[^/?#\s]+)(?P<path>/[^?#]*)?(?:[?#].*)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PublicUrl:
    """A parsed public Yandex.Disk link.

    ``path`` is the folder inside the public resource the link points to ("" for the root).
    It is URL-decoded once, so both links copied from the browser address bar
    (``.../d/KEY/%D0%9F%D0%B0%D0%BF%D0%BA%D0%B0``) and "pretty" ones (``.../d/KEY/Папка``)
    work. ``raw_path`` keeps the undecoded form as a fallback for names containing ``%``.
    """

    public_key: str
    path: str = ""
    raw_path: str = ""
    kind: str = "d"

    @property
    def key(self) -> str:
        return self.public_key.rstrip("/").rsplit("/", 1)[-1]


def parse_public_url(url: str) -> Optional[PublicUrl]:
    """Parse a public Yandex.Disk URL, returning ``None`` if it is not one."""
    if not url:
        return None
    match = _PUBLIC_URL_RE.match(url)
    if not match:
        return None
    host = match.group("host").lower()
    if host != "yadi.sk":
        host = "disk.yandex.ru"
    kind = match.group("kind").lower()
    public_key = f"https://{host}/{kind}/{match.group('key')}"
    raw_path = (match.group("path") or "").rstrip("/")
    path = unquote(raw_path)
    return PublicUrl(public_key=public_key, path=path, raw_path=raw_path, kind=kind)


class ApiError(Exception):
    """Yandex.Disk API request failed."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _describe_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
        text = data.get("description") or data.get("message") or data.get("error")
    except ValueError:
        text = None
    return f"HTTP {resp.status_code}" + (f": {text}" if text else "")


def retry_after_seconds(resp: requests.Response, default: int = 30) -> int:
    try:
        value = int(resp.headers.get("Retry-After", default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, 300))


class YandexDiskClient:
    """Client for interacting with Yandex.Disk API.

    Every thread gets its own HTTP session, so one client can be shared by download workers.
    """

    def __init__(self, verbose: bool = False, cancel_event: Optional[threading.Event] = None):
        self.verbose = verbose
        self.cancel_event = cancel_event or threading.Event()
        self.api_url = config.api_resources_url
        self.api_download_url = config.api_download_url
        self._local = threading.local()
        self._session_override: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        if self._session_override is not None:
            return self._session_override
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._create_session_with_retries()
            self._local.session = session
        return session

    @session.setter
    def session(self, value: requests.Session) -> None:
        self._session_override = value

    def reset_session(self) -> None:
        """Drop the current thread's session (and its possibly dead connections)."""
        session = getattr(self._local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            self._local.session = None

    def _create_session_with_retries(self) -> requests.Session:
        """Create a requests session with retry logic and rate limiting handling."""
        session = requests.Session()
        retry_strategy = Retry(
            total=config.max_retries,
            backoff_factor=config.retry_backoff_factor,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        headers = {"User-Agent": config.user_agent or ""}
        session.headers.update(headers)
        return session

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def sleep(self, seconds: float) -> None:
        """Sleep that is interrupted when the user cancels."""
        if self.cancel_event.wait(seconds):
            raise Cancelled()

    def handle_rate_limit(self, resp: requests.Response) -> None:
        """Handle rate limiting by waiting for the specified time."""
        if resp.status_code == 429:
            retry_after = retry_after_seconds(resp)
            logger.warning("Rate limited. Waiting %s seconds...", retry_after)
            self.sleep(retry_after)

    @staticmethod
    def validate_yandex_url(url: str) -> bool:
        """Validate Yandex.Disk public resource URL."""
        return parse_public_url(url) is not None

    def _api_get(self, url: str, params: Dict[str, Any]) -> requests.Response:
        """GET an API endpoint, retrying on network errors, rate limiting and 5xx."""
        last_error: str = "unknown error"
        for attempt in range(1, config.api_attempts + 1):
            self.check_cancel()
            if attempt > 1:
                self.sleep(min(config.api_retry_delay * 2 ** (attempt - 2), 60))
            try:
                resp = self.session.get(url, params=params, timeout=config.timeout)
            except requests.RequestException as e:
                last_error = f"no response from server ({e.__class__.__name__}: {e})"
                logger.debug("API request %s %s failed: %s", url, params, e)
                self.reset_session()
                continue
            if resp.status_code == 429:
                last_error = "rate limited (HTTP 429)"
                self.handle_rate_limit(resp)
                continue
            if resp.status_code >= 500:
                last_error = _describe_error(resp)
                logger.debug("API request %s %s failed: %s", url, params, last_error)
                continue
            if resp.status_code >= 400:
                raise ApiError(_describe_error(resp), resp.status_code)
            try:
                resp.json()
            except ValueError:
                last_error = "invalid JSON in API response"
                continue
            return resp
        raise ApiError(f"API request failed after {config.api_attempts} attempts: {last_error}")

    def get_resource_metadata(
        self,
        url: str,
        path: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> requests.Response:
        """Fetch resource metadata from Yandex.Disk API.

        ``url`` is the public key (the public link itself works as a key).
        """
        params: Dict[str, Any] = {"public_key": url, "limit": limit}
        if path:
            params["path"] = path
        if offset:
            params["offset"] = offset
        return self._api_get(self.api_url, params)

    def get_listing(self, url: str, path: Optional[str] = None) -> Dict[str, Any]:
        """Fetch metadata of a resource with *all* items of a folder (follows pagination)."""
        limit = config.list_page_size
        data: Dict[str, Any] = self.get_resource_metadata(url, path=path, limit=limit).json()
        embedded = data.get("_embedded")
        if data.get("type") != "dir" or not isinstance(embedded, dict):
            return data
        items = list(embedded.get("items") or [])
        total = embedded.get("total")
        while total is not None and len(items) < total:
            page = self.get_resource_metadata(url, path=path, limit=limit, offset=len(items))
            page_items = (page.json().get("_embedded") or {}).get("items") or []
            if not page_items:
                break
            items.extend(page_items)
        embedded["items"] = items
        embedded["offset"] = 0
        embedded["limit"] = len(items)
        return data

    def get_download_link(self, url: str, path: Optional[str] = None) -> str:
        """Get the direct download link for a public resource (or a file inside it)."""
        params: Dict[str, Any] = {"public_key": url}
        if path:
            params["path"] = path
        data = self._api_get(self.api_download_url, params).json()
        if "href" not in data:
            raise ApiError(f"No download URL found for {url} {path or ''}".rstrip())
        return str(data["href"])
