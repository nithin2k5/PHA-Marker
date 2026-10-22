"""Regression test on the real reference frame (black cable, three narrow grey
stripes, 'HCA' print, cluttered metal fixture).

The image is kept out of version control (samples/real/ is git-ignored), so
this test is skipped where it is not present.  The defect variants are edited
copies of the one real frame - useful for regression, but NOT evidence of
production accuracy, which needs many real good and defective cables.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from marker_engine.calibration import TeachSession
from marker_engine.detection import detect
from marker_engine.evaluation import FAIL, INDETERMINATE, NO_CABLE, PASS, evaluate
from marker_engine.recipes import Recipe

REF = Path(__file__).resolve().parent.parent / "samples" / "real" / "reference.png"
pytestmark = pytest.mark.skipif(not REF.exists(), reason="real reference image not available")

ROI = (250, 110, 700, 100)
REGION = (420, 120, 260, 80)        # includes the 'HCA' print next to the stripes


@pytest.fixture(scope="module")
def frame():
    return cv2.imread(str(REF))


@pytest.fixture(scope="module")
def recipe(frame):
    r = Recipe("BLK-3xGRY", body_colour="black", stripe_colour="grey", stripe_count=3,
               roi=ROI, inspection_region=REGION, image_size=(frame.shape[1], frame.shape[0]))
    teach_view = r.copy()
    teach_view.inspection_region = (420, 125, 160, 70)   # stripes only
    t = TeachSession()
    ok, msg = t.add_auto(frame, teach_view)
    assert ok, msg
    t.build(r)
    return r


def stripe_columns(frame, recipe):
    m = detect(frame, recipe)
    return [(int(s.polygon[:, 0].min()) - 2, int(s.polygon[:, 0].max()) + 3) for s in m.stripes]


def run(img, recipe, presets):
    m = detect(img, recipe)
    return m, evaluate(m, recipe, presets)


def test_good_cable_passes_with_print_in_region(frame, recipe, presets):
    m, v = run(frame, recipe, presets)
    assert v.status == PASS, v.reasons
    assert len(m.stripes) == 3
    assert any("stripe group" in c.reason for c in m.rejected)   # the print was seen and rejected


def test_missing_stripe(frame, recipe, presets):
    (_, _), (a0, a1), _ = stripe_columns(frame, recipe)
    img = frame.copy()
    img[140:190, a0 - 1:a1 + 1] = frame[140:190, a1 + 30:a1 + 32 + (a1 - a0)]
    _, v = run(img, recipe, presets)
    assert v.status == FAIL and "missing stripe" in v.reasons[0]


def test_extra_stripe(frame, recipe, presets):
    cols = stripe_columns(frame, recipe)
    img = frame.copy()
    dx = 20 + cols[2][0] - cols[0][0]
    dy = -int(round(dx * np.tan(np.radians(2.75))))       # follow the cable slope
    img[140 + dy:190 + dy, cols[0][0] + dx:cols[0][1] + dx] = frame[140:190, cols[0][0]:cols[0][1]]
    _, v = run(img, recipe, presets)
    assert v.status == FAIL and "extra stripe" in v.reasons[0]


def test_recoloured_stripe(frame, recipe, presets):
    _, (a0, a1), _ = stripe_columns(frame, recipe)
    img = frame.copy()
    patch = img[150:182, a0:a1].astype(np.float32)
    img[150:182, a0:a1] = np.clip(patch.mean(2, keepdims=True) / 255 * [40, 40, 230] * 1.6, 0, 255)
    _, v = run(img, recipe, presets)
    assert v.status == FAIL and "stripe 2 wrong colour" in v.reasons[0]


def test_wrong_recipe_body_colour(frame, recipe, presets):
    r = recipe.copy()
    r.body_colour = "blue"
    _, v = run(frame, r, presets)
    assert v.status == FAIL and "wrong cable colour" in v.reasons[0]


@pytest.mark.parametrize("factor", [0.6, 0.45, 1.3])
def test_lighting_change_is_not_judged(frame, recipe, presets, factor):
    img = np.clip(frame.astype(np.float32) * factor, 0, 255).astype(np.uint8)
    _, v = run(img, recipe, presets)
    assert v.status == INDETERMINATE


def test_heavy_blur_is_not_judged(frame, recipe, presets):
    _, v = run(cv2.GaussianBlur(frame, (0, 0), 4), recipe, presets)
    assert v.status == INDETERMINATE


def test_finger_over_stripes(frame, recipe, presets):
    img = frame.copy()
    cv2.rectangle(img, (468, 100), (488, 175), (225, 225, 230), -1)
    _, v = run(img, recipe, presets)
    assert v.status == INDETERMINATE


def test_shifted_roi_still_passes(frame, recipe, presets):
    r = recipe.copy()
    r.roi, r.inspection_region = (265, 118, 700, 100), (435, 128, 260, 80)
    _, v = run(frame, r, presets)
    assert v.status == PASS, v.reasons


def test_empty_fixture_is_no_cable(frame, recipe, presets):
    r = recipe.copy()
    r.roi, r.inspection_region = (1350, 250, 400, 90), (1500, 260, 120, 70)
    _, v = run(frame, r, presets)
    assert v.status == NO_CABLE


SET2 = REF.parent / "set2"
SET2_EXPECTED = {
    "01_green": FAIL, "02_red": FAIL, "03_orange": FAIL, "04_pink": FAIL, "05_blue": FAIL,
    "06_grey": PASS,             # same cable type as the reference frame
    "07_white": INDETERMINATE,   # white stripes are clipped (255) in this frame
    "08_yellow": FAIL,
}


@pytest.mark.skipif(not SET2.exists(), reason="second set of real frames not available")
@pytest.mark.parametrize("stem,expected", sorted(SET2_EXPECTED.items()))
def test_second_set_against_grey_recipe(stem, expected, recipe, presets):
    """Frames of other cable types, judged by the recipe taught only on reference.png."""
    img = cv2.imread(str(SET2 / f"{stem}.png"))
    m, v = run(img, recipe, presets)
    assert len(m.stripes) == 3
    assert v.status == expected, v.reasons
    if expected == FAIL:
        assert "wrong colour" in v.reasons[0]


SET3 = REF.parent / "set3" / "ref14.png"


@pytest.mark.skipif(not SET3.exists(), reason="re-framed real frame not available")
def test_reframed_camera_view_passes_without_redrawing(frame, recipe, presets):
    """Different crop/framing of the same fixture: alignment must follow it."""
    m, v = run(cv2.imread(str(SET3)), recipe, presets)
    assert m.alignment is not None and m.alignment.score >= 0.4
    assert v.status == PASS, v.reasons
    assert len(m.stripes) == 3
