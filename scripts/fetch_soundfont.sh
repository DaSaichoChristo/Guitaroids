#!/usr/bin/env bash
# Fetch a soundfont into assets/ for tinysoundfont to render with.
#
# Why not use a system soundfont: the one installed on many Linux desktops is
# TimGM6mb, which is GPL-2. Vendoring that would impose GPL-2 on this project, so
# we fetch FluidR3 instead, which Frank Wen releases under the MIT licence.
#
# FluidR3 mono is preferred over the full stereo build: ~18MB instead of ~114MB,
# MIT either way, and mono is perfectly adequate for a backing track.
#
# Note the packaged file is FluidR3Mono_GM.sf3, an SF3 (RIFF-wrapped SF2), not a
# plain .sf2. tinysoundfont handles sf2/sf3/sfo, so we accept either extension and
# keep it.
#
# This is optional. Without it the numpy Karplus-Strong synth is used instead, so
# a failure here is not fatal.
set -euo pipefail

cd "$(dirname "$0")/.."

DEB_URL="http://deb.debian.org/debian/pool/main/f/fluidr3mono-gm-soundfont/fluidr3mono-gm-soundfont_2.315-7_all.deb"

# Already fetched (either extension)?
for existing in assets/soundfont.sf2 assets/soundfont.sf3; do
    if [ -f "$existing" ]; then
        echo "==> Already present: $existing ($(stat -c%s "$existing") bytes)"
        exit 0
    fi
done

mkdir -p assets
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if [ -n "${GUITAROIDS_SOUNDFONT:-}" ] && [ -f "${GUITAROIDS_SOUNDFONT}" ]; then
    case "$GUITAROIDS_SOUNDFONT" in
        *.sf2|*.sf3) DEST="assets/soundfont.${GUITAROIDS_SOUNDFONT##*.}" ;;
        *)           DEST="assets/soundfont.sf2" ;;
    esac
    echo "==> Using soundfont from \$GUITAROIDS_SOUNDFONT"
    cp "$GUITAROIDS_SOUNDFONT" "$DEST"
    echo "==> Saved $DEST ($(stat -c%s "$DEST") bytes)"
    exit 0
fi

echo "==> Downloading FluidR3 mono soundfont (MIT, ~18MB)"
if ! curl -fsSL "$DEB_URL" -o "$TMP/sf.deb"; then
    echo "    download failed - falling back to numpy synth" >&2
    exit 1
fi

echo "==> Extracting"
# dpkg-deb where available, else ar + tar, so this works without dpkg.
if command -v dpkg-deb >/dev/null 2>&1; then
    dpkg-deb -x "$TMP/sf.deb" "$TMP/x"
else
    ( cd "$TMP" && ar x sf.deb && tar xf data.tar.* )
fi

SF2=$(find "$TMP" \( -name '*.sf2' -o -name '*.sf3' \) | head -1)
if [ -z "$SF2" ]; then
    echo "    no .sf2/.sf3 inside the package - falling back to numpy synth" >&2
    exit 1
fi

case "$SF2" in
    *.sf3) DEST="assets/soundfont.sf3" ;;
    *)     DEST="assets/soundfont.sf2" ;;
esac
cp "$SF2" "$DEST"
echo "==> Saved $DEST ($(stat -c%s "$DEST") bytes)"
echo "    FluidR3 (c) Frank Wen, MIT licence. See assets/ATTRIBUTION-soundfont.md"
