"""Scenario tests on synthetic fixture images.

The recipe is taught on seeds 1-4; evaluation uses seed 20+ so calibration
and evaluation images are different.  Synthetic images only check the logic -
they say nothing about accuracy on real cables (see README, validation).
"""

import pytest

from cable_inspection import synthetic
from cable_inspection.detection import ABSENT, PRESENT, detect
from cable_inspection.evaluation import (AMBIGUOUS, FAIL, INDETERMINATE, NO_CABLE, PASS, UNKNOWN, evaluate,
                                         identify)

SCENARIOS = [
    # name, scene kwargs, expected verdict, text expected in the reason
    ("correct cable", {}, PASS, "verified"),
    ("slight rotation +5", {"angle_deg": 5}, PASS, "verified"),
    ("slight rotation -7 and shift", {"angle_deg": -7, "shift": (10, -12)}, PASS, "verified"),
    ("position shift", {"shift": (25, 8)}, PASS, "verified"),
    ("printed text in region", {"text_in_region": True}, PASS, "verified"),
    ("mild glare", {"glare": 0.4}, PASS, "verified"),
    ("wrong stripe colour", {"stripe": "red"}, FAIL, "wrong colour"),
    ("one wrong stripe", {"stripe_colours": ["white", "yellow", "white"]}, FAIL, "stripe 2 wrong colour"),
    ("wrong body colour", {"body": "blue"}, FAIL, "wrong cable colour"),
    ("missing stripe", {"stripe_count": 2}, FAIL, "missing stripe"),
    ("extra stripe", {"stripe_count": 4}, FAIL, "extra stripe"),
    ("strong glare", {"glare": 1.0}, INDETERMINATE, "glare"),
    ("occlusion", {"occlusion": True}, INDETERMINATE, "crossing"),
    ("blurred", {"blur": 5.0}, INDETERMINATE, "blurred"),
    ("stripe cut by region border", {"partial_stripe": True}, INDETERMINATE, "border"),
    ("too dark", {"brightness": 0.03}, INDETERMINATE, "dark"),
    ("lighting changed", {"brightness": 0.6}, INDETERMINATE, "illumination"),
    ("overexposed stripes", {"brightness": 1.15}, INDETERMINATE, "saturated"),
    ("no cable", {"no_cable": True}, NO_CABLE, "no cable"),
]


@pytest.mark.parametrize("name,kw,expected,reason", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_scenario(name, kw, expected, reason, taught_recipe, presets):
    scene = synthetic.make(seed=20, **kw)
    m = detect(scene.image, taught_recipe)
    v = evaluate(m, taught_recipe, presets)
    assert v.status == expected, (v.reasons, m.issues)
    assert any(reason in r for r in v.reasons), v.reasons


@pytest.mark.parametrize("seed", range(21, 26))
def test_good_cables_pass_across_seeds(seed, taught_recipe, presets):
    m = detect(synthetic.make(seed=seed, angle_deg=(seed % 5) - 2).image, taught_recipe)
    assert evaluate(m, taught_recipe, presets).status == PASS


def test_wrong_colour_stripes_are_found_before_colour_check(taught_recipe, presets):
    m = detect(synthetic.make(seed=20, stripe="green").image, taught_recipe)
    assert len(m.stripes) == 3                       # found as physical stripes
    v = evaluate(m, taught_recipe, presets)
    assert v.status == FAIL
    assert all(s.colour_ok is False for s in m.stripes)


def test_stripe_measurements(taught_recipe):
    scene = synthetic.make(seed=20)
    m = detect(scene.image, taught_recipe)
    assert m.presence == PRESENT
    widths = [s.width_px for s in m.stripes]
    centres = [s.centre_px for s in m.stripes]
    assert all(7 <= w <= 13 for w in widths)                     # drawn 9 px
    spacing = [b - a for a, b in zip(centres, centres[1:])]
    assert all(abs(sp - 42) <= 2 for sp in spacing)              # drawn 42 px
    assert abs(m.cable_width_px - 64) <= 3


def test_printed_text_rejected_not_counted(taught_recipe):
    m = detect(synthetic.make(seed=20, text_in_region=True).image, taught_recipe)
    assert len(m.stripes) == 3


def test_presence_is_colour_independent(taught_recipe):
    for body, stripe in (("white", "black"), ("yellow", "blue"), ("green", "white")):
        m = detect(synthetic.make(seed=20, body=body, stripe=stripe).image, taught_recipe)
        assert m.presence == PRESENT, (body, m.notes, m.issues)
        assert len(m.stripes) == 3


def test_no_cable_is_absent(taught_recipe):
    assert detect(synthetic.make(seed=7, no_cable=True).image, taught_recipe).presence == ABSENT


def test_spacing_check(taught_recipe, presets):
    r = taught_recipe.copy()
    r.geometry["check_spacing"] = True
    assert evaluate(detect(synthetic.make(seed=20).image, r), r, presets).status == PASS
    wide = synthetic.make(seed=20, stripe_spacing=56)
    v = evaluate(detect(wide.image, r), r, presets)
    assert v.status == FAIL and any("spacing" in x for x in v.reasons)


def test_identification(taught_recipe, presets):
    base = taught_recipe
    red = base.copy()
    red.name, red.stripe_colour, red.stripe_ref = "BLK-3xRED", "red", None
    two = base.copy()
    two.name, two.stripe_count = "BLK-2xWHT", 2
    recipes = [base, red, two]
    m = detect(synthetic.make(seed=20).image, base)
    assert identify(m, recipes, presets)[0] == base.name
    m = detect(synthetic.make(seed=20, stripe="red").image, base)
    assert identify(m, recipes, presets)[0] == "BLK-3xRED"
    m = detect(synthetic.make(seed=20, body="green").image, base)
    assert identify(m, recipes, presets)[0] == UNKNOWN
    dup = base.copy()
    dup.name = "DUPLICATE"
    m = detect(synthetic.make(seed=20).image, base)
    assert identify(m, recipes + [dup], presets)[0] == AMBIGUOUS


def test_stripe_region_off_the_cable_is_not_a_fail(taught_recipe, presets):
    """A misplaced stripe region is a setup problem, never 'missing stripes'."""
    r = taught_recipe.copy()
    x, y, w, h = r.inspection_region
    r.inspection_region = (x + 150, y + 110, w, h)   # below the cable, away from the stripes
    v = evaluate(detect(synthetic.make(seed=20).image, r), r, presets)
    assert v.status == INDETERMINATE
    assert any("outside the stripe region" in s for s in v.reasons)
