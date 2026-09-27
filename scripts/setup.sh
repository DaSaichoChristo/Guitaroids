#!/usr/bin/env bash
# Reproducible setup. Installs dependencies in the one order that works.
# This is the ONLY supported install path -- see requirements.txt for why a bare
# `pip install -r requirements.txt` is not equivalent.
#
# PowerShell equivalent: scripts/setup.ps1
# KEEP THE TWO IN SYNC -- tests/test_setup_scripts.py asserts they agree on the
# critical pins (the soundfont URL, and that neither has an OpenCV step).
#
# Why there is still an order to it (DESIGN.md §7.5):
#   tinysoundfont is installed with --no-deps, because pyaudio has no wheel and
#   needs portaudio19-dev. We render offline, and pyaudio is a lazy import used
#   solely by Synth.start(), which we do not call. Beware: `pip install --dry-run`
#   exits 0 on tinysoundfont even though a real install fails, so a dry run is not
#   evidence either way.
#
# There used to be an OpenCV dance here -- uninstall the GUI build, install the
# headless one, in that order, because both write to the same cv2/ directory. It
# existed entirely because mediapipe requires the GUI build, and mediapipe existed
# entirely because we were going to track hands with a webcam. We are not
# (DESIGN.md §25). The trap is gone, and so is the caveat in requirements.txt.
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

echo "==> Installing optional packages that need --no-deps (requirements-optional.txt)"
if $PY -m pip install -r requirements-optional.txt --no-deps --quiet; then
    echo "    ok - $(grep -c '^[a-zA-Z]' requirements-optional.txt || echo 0) package(s)"
else
    echo "    FAILED - continuing. The numpy pluck synth is the fallback, so audio"
    echo "    still renders without it. See requirements-optional.txt."
fi

echo "==> Fetching a soundfont (optional, used by tinysoundfont)"
scripts/fetch_soundfont.sh || echo "    skipped - numpy synth will be used instead"

echo
echo "==> Verifying the M0 gate"
$PY -m pytest tests/test_m0_window.py -q

echo
echo "Setup complete. See DESIGN.md §7 for track choice and audio synthesis."
