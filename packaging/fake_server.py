"""Run the fake Yandex.Disk API from the tests, for checking a packaged build.

    python packaging/fake_server.py URL_FILE

Writes the API base URL into URL_FILE and serves until killed.
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.fake_yandex import FakeYandexDisk  # noqa: E402

TREE = {
    '/Папка/b "quoted".txt': b"quoted name\n",
    "/Папка/c%3Fd.txt": b"percent name\n",
    "/big.bin": bytes(range(256)) * 40000,  # ~10 MB: downloaded in segments
}


def main() -> None:
    with FakeYandexDisk(dict(TREE)) as disk:
        with open(sys.argv[1], "w", encoding="utf8") as f:
            f.write(disk.base + "/v1/disk")
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
