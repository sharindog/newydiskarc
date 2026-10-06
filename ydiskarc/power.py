"""Keep the computer awake while a download is running."""

import logging
import os
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# SetThreadExecutionState flags (Windows)
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    """Context manager that prevents idle system sleep (the display may still turn off).

    macOS: runs ``caffeinate -i -s`` bound to this process; Windows: SetThreadExecutionState.
    Elsewhere it does nothing. Failures are ignored - sleep prevention is a convenience.
    """

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._windows = False

    def __enter__(self) -> "KeepAwake":
        try:
            if sys.platform == "darwin":
                self._process = subprocess.Popen(
                    ["caffeinate", "-i", "-s", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "win32":
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
                    _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
                )
                self._windows = True
        except Exception as e:
            logger.debug("Cannot prevent system sleep: %s", e)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                pass
            self._process = None
        if self._windows:
            try:
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
                    _ES_CONTINUOUS
                )
            except Exception:
                pass
            self._windows = False
