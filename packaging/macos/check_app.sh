#!/bin/bash
# Check a built dist/ydiskarc.app: architecture, GUI start and a real download
# (against the fake API from the tests) with the packaged binary.
set -euo pipefail

BIN="dist/ydiskarc.app/Contents/MacOS/ydiskarc"
PY="${PYTHON:-python}"
EXPECTED_ARCH="${1:-arm64}"

echo "== architecture"
file "$BIN"
lipo -archs "$BIN" | grep -qw "$EXPECTED_ARCH"

echo "== version"
"$BIN" version

echo "== GUI starts"
"$BIN" --smoke-test

echo "== download with the packaged binary"
WORK="$(mktemp -d)"
"$PY" packaging/fake_server.py "$WORK/url" &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 50); do [ -s "$WORK/url" ] && break; sleep 0.2; done
YDISKARC_API_BASE_URL="$(cat "$WORK/url")" "$BIN" sync https://disk.yandex.ru/d/Test \
    -o "$WORK/out" -t 2 -c 4
test "$(cat "$WORK/out/Папка/c%3Fd.txt")" = "percent name"
test "$(cat "$WORK/out/Папка/b \"quoted\".txt")" = "quoted name"
test "$(stat -f %z "$WORK/out/big.bin")" = "10240000"
echo "all checks passed"
