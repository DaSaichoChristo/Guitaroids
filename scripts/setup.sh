#!/usr/bin/env bash
# Reproducible setup. Installs dependencies in the one order that works.
# This is the ONLY supported install path -- see requirements.txt for why a bare
# `pip install -r requirements.txt` is not equivalent.
#
# PowerShell equivalent: scripts/setup.ps1
# KEEP THE TWO IN SYNC -- tests/test_setup_scripts.py asserts they agree on the
# critical pins (opencv version, the model URL, the soundfont URL).
#
# Why the order matters (DESIGN.md §2.2, §7.4):
#   1. mediapipe depends on opencv-contrib-python, the GUI build. Its bundled Qt
#      plugins under cv2/qt/plugins override QT_PLUGIN_PATH and break PySide6.
#   2. Both OpenCV builds write to the same cv2/ directory, so the GUI build must
#      be uninstalled BEFORE the headless build is installed. The reverse order
#      deletes the headless files and breaks `import cv2`. This bit us during
#      testing and is now asserted by tests/test_m0_window.py.
#   3. tinysoundfont is installed with --no-deps, because pyaudio has no wheel and
#      needs portaudio19-dev. We only render offline, and pyaudio is a lazy import
#      used solely for real-time playback. Beware: `pip install --dry-run` exits 0
#      on tinysoundfont even though a real install fails, so a dry run is not
#      evidence either way.
set -euo pipefail

cd "$(dirname "$0")/.."
PY=.venv/bin/python

if [ ! -x "$PY" ]; then
    echo "==> Creating venv (override with PYTHON=python3.X)"
    "${PYTHON:-python3.12}" -m venv .venv
fi
$PY -m pip install --upgrade pip --quiet

echo "==> Checking the interpreter"
$PY --version
PYVER=$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')
case "$PYVER" in
    3.12) echo "    known-good: every dependency including tinysoundfont has a wheel" ;;
    3.10) echo "    known-good: wheels for everything; numpy is capped at 2.2.6" ;;
    3.14) echo "    WARNING: no tinysoundfont wheel, so it builds from source," ;;
    *)    echo "    WARNING: untested version; tinysoundfont may fail to build." ;;
esac
if [ "$PYVER" != "3.12" ] && [ "$PYVER" != "3.10" ]; then
    echo "    WARNING: that can need a C++ toolchain and Python dev headers."
fi

echo "==> Installing pinned dependencies"
$PY -m pip install -r requirements-dev.txt --quiet

# pip resolves mediapipe's opencv-contrib-python dep even though requirements.txt
# lists the headless variant, because the two are separate distributions.
#
# Both builds install into the SAME cv2/ directory, so they clobber each other.
# Removing only the GUI build leaves headless's dist-info behind with its files
# deleted, and pip then reports "already satisfied" and reinstalls nothing --
# leaving `import cv2` broken. So remove BOTH, then install headless cleanly.
GUI_VERSION=""
$PY -m pip show opencv-contrib-python >/dev/null 2>&1 && \
    GUI_VERSION=$($PY -m pip show opencv-contrib-python | awk '/^Version:/{print $2}')
$PY -m pip uninstall -y opencv-contrib-python opencv-contrib-python-headless --quiet || true
echo "==> Installing headless OpenCV build (GUI build ${GUI_VERSION:-(absent)} removed)"
$PY -m pip install "opencv-contrib-python-headless==5.0.0.93" --quiet

echo "==> Installing optional packages that need --no-deps (requirements-optional.txt)"
if $PY -m pip install -r requirements-optional.txt --no-deps --quiet; then
    echo "    ok - $(grep -c '^[a-zA-Z]' requirements-optional.txt || echo 0) package(s)"
else
    echo "    FAILED - continuing. The numpy Karplus-Strong synth is the fallback,"
    echo "    so audio still renders without it. See requirements-optional.txt."
fi

echo "==> Fetching the mediapipe hand landmarker model"
scripts/fetch_model.sh

echo "==> Fetching a soundfont (optional, used by tinysoundfont)"
scripts/fetch_soundfont.sh || echo "    skipped - numpy synth will be used instead"

echo
echo "==> Verifying the M0 gate"
$PY -m pytest tests/test_m0_window.py -q

echo
echo "Setup complete. See DESIGN.md §7 for track choice and audio synthesis."
