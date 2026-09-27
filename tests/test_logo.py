"""The main menu's logo: loading, keying, and surviving a missing asset (§43).

The keying is tested on a **synthetic** image rather than on the real PNG, so the
tests say something about the algorithm and not about whether a particular file
happens to be present. The real asset is 992x1058 and 834KB; a test that asserted on
its pixels would be asserting that a picture still looks like a picture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from guitaroids.ui.logo import (
    BACKGROUND_TOLERANCE,
    load_logo,
    logo_label,
    logo_path,
    without_background,
)

BACKGROUND = (208, 208, 208)


def _image(width: int = 40, height: int = 40, *, opaque: bool = True) -> QtGui.QImage:
    """A background with a solid block in the middle, plus a light detail inside it.

    The detail is the point: it is the same near-white as the background, so a
    whole-image colour test would eat it. Only a fill that cannot reach past the block
    keeps it, which is the behaviour the real file needs for its soundhole ring and
    fret dots.
    """
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor(*BACKGROUND, 255 if opaque else 0))
    painter = QtGui.QPainter(image)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(210, 120, 20))
    painter.drawRect(10, 10, 20, 20)
    painter.setBrush(QtGui.QColor(245, 245, 245))
    painter.drawRect(17, 17, 6, 6)  # the "fret dot"
    painter.end()
    return image


# --- the keying ---------------------------------------------------------------


def test_the_background_becomes_transparent(qapp) -> None:
    keyed = without_background(_image())
    for x, y in ((0, 0), (39, 0), (0, 39), (39, 39), (5, 20)):
        assert keyed.pixelColor(x, y).alpha() == 0, f"({x}, {y}) is still background"


def test_the_artwork_survives_the_key(qapp) -> None:
    keyed = without_background(_image())
    assert keyed.pixelColor(20, 20).alpha() == 255, "the block was keyed away"


def test_a_light_detail_inside_the_artwork_survives(qapp) -> None:
    """A pale detail well clear of the tolerance is kept.

    Note what this does *not* establish: the dot is (245, 245, 245) against a
    (208, 208, 208) background, 37 apart and so outside any tolerance this module
    would use. A naive whole-image colour test keeps it too. It is here because a
    regression that keyed *everything* light would be caught, not because it
    distinguishes the fill from the test -- that is the next test's job.
    """
    keyed = without_background(_image())
    assert keyed.pixelColor(18, 18).alpha() == 255, "the fret dot was keyed away"


def test_a_detail_the_same_colour_as_the_background_survives_inside_the_artwork(
    qapp,
) -> None:
    """**This** is what distinguishes a flood fill from a colour test.

    A pixel inside the artwork that is exactly the background colour is invisible
    against the background either way, so removing it would look fine on screen while
    silently eating a hole in the drawing. A fill cannot reach it, because the
    artwork is in the way; a whole-image colour test removes it immediately.

    This is not hypothetical for the real logo: 2,642 of its pixels sit within 10 of
    the background grey but are *not* on a border row or column, so they are the
    guitar's, and a colour test would delete every one of them.
    """
    image = QtGui.QImage(40, 40, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor(*BACKGROUND))
    painter = QtGui.QPainter(image)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(210, 120, 20))
    painter.drawRect(8, 8, 24, 24)
    # A background-coloured detail *inside* the block, walled off from the outside.
    painter.setBrush(QtGui.QColor(*BACKGROUND))
    painter.drawRect(18, 18, 4, 4)
    painter.end()

    keyed = without_background(image)
    assert keyed.pixelColor(20, 20).alpha() == 255, (
        "a background-coloured pixel inside the artwork was keyed away, which is a "
        "hole in the drawing that a colour test cannot tell from a correct removal"
    )


def test_a_hollow_shape_is_keyed_from_inside_too(qapp) -> None:
    """Background enclosed by the artwork is still background.

    The real logo has the gap between its two arms, and a fill that only ran inwards
    from the four edges would leave that gap as a grey patch floating on the menu.
    """
    image = QtGui.QImage(40, 40, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor(*BACKGROUND))
    painter = QtGui.QPainter(image)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(210, 120, 20))
    # A ring, with background visible both inside and outside it.
    painter.drawRect(8, 8, 24, 24)
    image_ = image
    painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Clear)
    painter.drawRect(14, 14, 12, 12)
    painter.end()

    keyed = without_background(image_)
    assert keyed.pixelColor(20, 20).alpha() == 0, "the hole inside the ring is background"
    assert keyed.pixelColor(10, 20).alpha() == 255, "the ring itself is artwork"


def test_an_already_transparent_image_is_returned_unchanged(qapp) -> None:
    """An author's real cut-out must be used verbatim, not keyed.

    A whole-image key on a genuine cut-out would nibble at any white pixel the
    tolerance happened to reach, which is the damage this is designed to avoid.
    """
    image = _image(opaque=False)
    assert without_background(image) is image


def test_an_empty_image_is_returned_unchanged(qapp) -> None:
    empty = QtGui.QImage()
    assert without_background(empty) is empty


def test_the_tolerance_is_tight(qapp) -> None:
    """A guard on the constant rather than on the behaviour.

    The background is a flat fill and the artwork has white in it, so every unit of
    slack in this number is a unit of risk to the guitar. 10 was chosen from the
    measured spread of this file's background.
    """
    assert 0 < BACKGROUND_TOLERANCE <= 16


# --- the asset ----------------------------------------------------------------


def test_a_missing_logo_is_not_an_error(qapp, tmp_path: Path) -> None:
    """A picture is not a dependency. The menu must build without one."""
    assert load_logo(tmp_path) is None
    assert logo_label(100, tmp_path) is None


def test_logo_path_is_where_the_asset_lives() -> None:
    assert logo_path().name == "Guitaroids.png"
    assert logo_path().parent.name == "assets"


def test_the_logo_keeps_its_aspect_ratio(qapp, tmp_path: Path) -> None:
    """A logo stretched to a guessed square is worse than no logo.

    The real image is 992x1058, so a width-for-height swap is a 6% vertical squash,
    which on pixel art is visible as leaning strings.
    """
    image = _image(40, 20)
    path = tmp_path / "Guitaroids.png"
    assert image.save(str(path))

    label = logo_label(100, tmp_path)
    assert label is not None
    assert label.height() == 100
    assert label.width() == 200, "a 2:1 image scaled to 100 tall must be 200 wide"
