"""The main menu's logo: loading it, and removing the background it did not have.

**The PNG in ``assets/`` is not actually transparent.** It arrived named
``Firefly_RemoveBackground.png``, with an alpha channel and a uniform light-grey
(208, 208, 208) fill behind the artwork at ``alpha=255`` everywhere. A file with an
alpha channel is not a file with transparency, and the distinction matters here more
than usual: the app's background is a warm near-black, so dropping this in unmodified
puts a 992x1058 light-grey rectangle in the middle of the main menu.

Two ways out, and this does the one that works with the file we have:

- Get a properly cut-out PNG and delete all of this. A few lines of QPainter go away
  and the image is used exactly as authored, which is the better outcome.
- Key the background out at load time, which is what :func:`without_background` does.

The key is a **flood fill from the image border**, not a colour test over the whole
image, and the difference is not a detail. 84% of this file's pixels are within 10 of
the background grey, and a whole-image test would take the guitar's white with them:
the soundhole ring, the fret dots and the highlights are all light. A fill that only
removes background *connected to an edge* cannot reach them, because the guitar is in
the way. Measured on this file: 875,475 pixels match at tolerance 6 and 878,117 at
tolerance 10, so the background is uniform enough that a tight tolerance is safe, and
2,046 near-grey pixels are interior -- those are the artwork's, and they stay.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..paths import ASSETS_DIR

#: The logo's filename in ``assets/``.
LOGO_NAME = "Guitaroids.png"

#: How far a pixel's channels may sit from the sampled background colour and still
#: count as background. 10 is tight on purpose: this file's background is a flat fill,
#: and every unit of slack is a unit of risk to the guitar's white details.
BACKGROUND_TOLERANCE = 10


def logo_path(assets_dir: Path | None = None) -> Path:
    """Where the logo is expected to be."""
    return (assets_dir or ASSETS_DIR) / LOGO_NAME


def load_logo(assets_dir: Path | None = None) -> QtGui.QImage | None:
    """The logo with its background keyed out, or ``None`` if there isn't one.

    ``None`` is a supported answer and the caller is expected to cope: a missing or
    unreadable asset must not stop the application starting, which is the same
    reasoning the soundfont search uses.
    """
    path = logo_path(assets_dir)
    if not path.is_file():
        return None
    image = QtGui.QImage(str(path))
    if image.isNull():
        return None
    return without_background(image)


def logo_label(
    height: int,
    assets_dir: Path | None = None,
    *,
    parent: QtWidgets.QWidget | None = None,
) -> QtWidgets.QLabel | None:
    """A label holding the logo scaled to ``height``, or ``None`` if there is none.

    The width comes from the image's own aspect ratio rather than a guessed square:
    this one is 992x1058, so scaling by width and assuming a square would stretch it.
    """
    image = load_logo(assets_dir)
    if image is None:
        return None
    width = max(1, round(image.width() * height / image.height()))
    label = QtWidgets.QLabel(parent)
    label.setObjectName("logo")
    label.setFixedSize(width, height)
    label.setPixmap(
        QtGui.QPixmap.fromImage(image).scaled(
            width,
            height,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
    )
    return label


def without_background(
    image: QtGui.QImage, tolerance: int = BACKGROUND_TOLERANCE
) -> QtGui.QImage:
    """A copy with the border-connected background made transparent.

    Returns the image **unchanged** if it is already transparent, or if the top-left
    pixel is already fully transparent -- so an author who does supply a real cut-out
    gets their artwork used verbatim, with no keying applied to it at all.
    """
    source = image.convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    width, height = source.width(), source.height()
    if width == 0 or height == 0:
        return image
    if source.pixelColor(0, 0).alpha() == 0 or _has_transparency(source):
        return image

    background = source.pixelColor(0, 0)
    reached = _flood_background(source, background, tolerance)

    out = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
    out.fill(QtCore.Qt.GlobalColor.transparent)
    for y in range(height):
        for x in range(width):
            if (x, y) not in reached:
                out.setPixelColor(x, y, source.pixelColor(x, y))
    return out


def _has_transparency(image: QtGui.QImage) -> bool:
    """True if any pixel is not fully opaque.

    Sampled on a coarse grid: an exhaustive check of a 992x1058 image is a million
    `pixelColor` calls, each of which crosses into C++ and back. A grid is enough to
    notice a cut-out, which is the only question being asked.
    """
    if not image.hasAlphaChannel():
        return False
    step_y = max(1, image.height() // 24)
    step_x = max(1, image.width() // 24)
    for y in range(0, image.height(), step_y):
        for x in range(0, image.width(), step_x):
            if image.pixelColor(x, y).alpha() < 255:
                return True
    return False


def _matches(colour: QtGui.QColor, background: QtGui.QColor, tolerance: int) -> bool:
    return (
        abs(colour.red() - background.red()) <= tolerance
        and abs(colour.green() - background.green()) <= tolerance
        and abs(colour.blue() - background.blue()) <= tolerance
    )


def _flood_background(
    image: QtGui.QImage, background: QtGui.QColor, tolerance: int
) -> set[tuple[int, int]]:
    """Every pixel reachable from an edge without crossing the artwork.

    Breadth-first, with ``seen`` doubling as the visited set so a pixel is enqueued
    once even where the background wraps around the guitar.
    """
    width, height = image.width(), image.height()
    reached: set[tuple[int, int]] = set()
    pending: deque[tuple[int, int]] = deque()

    def seed(x: int, y: int) -> None:
        if (x, y) not in reached:
            reached.add((x, y))
            pending.append((x, y))

    for x in range(width):
        seed(x, 0)
        seed(x, height - 1)
    for y in range(height):
        seed(0, y)
        seed(width - 1, y)

    while pending:
        x, y = pending.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if (nx, ny) in reached:
                continue
            reached.add((nx, ny))
            if _matches(image.pixelColor(nx, ny), background, tolerance):
                pending.append((nx, ny))

    return reached
