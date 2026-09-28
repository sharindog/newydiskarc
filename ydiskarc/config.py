"""Configuration module for ydiskarc."""

from dataclasses import dataclass
from typing import Optional, Tuple

import ydiskarc


@dataclass
class Config:
    """Configuration settings for ydiskarc."""

    api_base_url: str = "https://cloud-api.yandex.net/v1/disk"
    api_resources: str = "/public/resources"
    api_download: str = "/public/resources/download"
    chunk_size: int = 256 * 1024  # 256KB chunks
    # Connect and read timeouts. The read timeout applies to every socket read, so a
    # connection that died (e.g. while the computer was sleeping) raises instead of hanging.
    connect_timeout: int = 15
    read_timeout: int = 60
    max_retries: int = 3  # low-level HTTP retries (connection errors, 429, 5xx)
    retry_backoff_factor: float = 1.0
    api_attempts: int = 6  # attempts for one API request
    download_attempts: int = 5  # attempts for one file within one pass
    retry_rounds: int = 3  # extra passes over failed files and folders
    retry_round_pause: int = 15  # seconds to wait before an extra pass
    api_retry_delay: float = 1.0  # first pause between API attempts, doubled each time
    download_retry_delay: float = 5.0  # first pause between download attempts, doubled
    list_page_size: int = 1000
    threads: int = 3
    max_threads: int = 64
    # Big files are downloaded in segments over several connections at once.
    connections_per_file: int = 4
    max_connections_per_file: int = 16
    segment_size: int = 16 * 1024 * 1024  # largest segment
    min_segment_size: int = 2 * 1024 * 1024  # files up to twice this use one connection
    part_suffix: str = ".ydpart"
    user_agent: Optional[str] = None  # Auto-generated if None

    def __post_init__(self) -> None:
        """Initialize user agent if not provided."""
        if self.user_agent is None:
            self.user_agent = (
                f"ydiskarc/{ydiskarc.__version__} (https://github.com/ruarxive/ydiskarc)"
            )

    @property
    def timeout(self) -> Tuple[int, int]:
        """Timeout tuple for requests."""
        return (self.connect_timeout, self.read_timeout)

    @property
    def api_resources_url(self) -> str:
        """Get full API resources URL."""
        return f"{self.api_base_url}{self.api_resources}"

    @property
    def api_download_url(self) -> str:
        """Get full API download URL."""
        return f"{self.api_base_url}{self.api_download}"


# Global configuration instance
config = Config()
