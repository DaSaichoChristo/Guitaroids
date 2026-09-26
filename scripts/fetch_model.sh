#!/usr/bin/env bash
# Fetch the mediapipe Hand Landmarker model into assets/.
#
# Vendored rather than committed (.gitignore excludes assets/*.task, ~7.8MB), so a
# fresh clone needs one command instead of a network fetch at first run.
set -euo pipefail

cd "$(dirname "$0")/.."
URL="https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
DEST="assets/hand_landmarker.task"

mkdir -p assets

if [ -f "$DEST" ]; then
    echo "==> Already present: $DEST ($(stat -c%s "$DEST") bytes)"
    exit 0
fi

echo "==> Downloading hand_landmarker.task"
curl -fsSL "$URL" -o "$DEST"
echo "==> Saved $DEST ($(stat -c%s "$DEST") bytes)"
