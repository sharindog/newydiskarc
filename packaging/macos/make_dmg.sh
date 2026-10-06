#!/bin/bash
# Pack dist/ydiskarc.app into dist/ydiskarc-<version>-macos-<arch>.dmg
# (the app plus a link to /Applications, for drag-and-drop installation).
set -euo pipefail

VERSION="$1"
ARCH="${2:-arm64}"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="dist/ydiskarc.app"
OUT="dist/ydiskarc-${VERSION}-macos-${ARCH}.dmg"

[ -d "$APP" ] || { echo "$APP not found" >&2; exit 1; }
STAGE="$(mktemp -d)/ydiskarc"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/ydiskarc.app"
ln -s /Applications "$STAGE/Applications"
cp "$HERE/README-DMG.txt" "$STAGE/Если не открывается.txt"

rm -f "$OUT"
# hdiutil occasionally fails with "Resource busy" on CI machines; retry a few times.
for attempt in 1 2 3 4 5; do
    if hdiutil create -volname "ydiskarc ${VERSION}" -srcfolder "$STAGE" \
        -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "$OUT"; then
        break
    fi
    [ "$attempt" = 5 ] && exit 1
    sleep $((attempt * 5))
done
hdiutil verify "$OUT"
echo "$OUT"
