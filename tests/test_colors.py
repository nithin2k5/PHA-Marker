import numpy as np
import pytest

from marker_engine.colors import (ColourReference, bgr_to_lab, delta_e_2000, nearest_colour_name)


@pytest.mark.parametrize("lab1,lab2,expected", [
    # Sharma, Wu & Dalal (2005) reference pairs
    ([50, 2.6772, -79.7751], [50, 0, -82.7485], 2.0425),
    ([50, 2.5, 0], [73, 25, -18], 27.1492),
    ([50, 2.5, 0], [50, 0, -2.5], 4.3065),
    ([60.2574, -34.0099, 36.2677], [60.4626, -34.1751, 39.4387], 1.2644),
])
def test_ciede2000_reference_values(lab1, lab2, expected):
    assert delta_e_2000(lab1, lab2) == pytest.approx(expected, abs=1e-3)


def test_bgr_to_lab_extremes():
    lab = bgr_to_lab(np.array([[0, 0, 0], [255, 255, 255]], np.uint8))
    assert lab[0, 0] == pytest.approx(0, abs=0.1)
    assert lab[1, 0] == pytest.approx(100, abs=0.1)


def test_achromatic_matching_ignores_hue_but_requires_low_chroma():
    black = ColourReference("black", [14, 0, 0])
    assert black.achromatic
    assert black.matches([16, 2, -2])
    assert not black.matches([16, 25, 20])  # dark red/brown is not black
    assert not black.matches([45, 0, 0])    # grey is not black


def test_pale_colour_needs_matching_hue():
    pale_yellow = ColourReference("pale yellow", [90, -3, 22])
    white = ColourReference("white", [92, 0, 0])
    assert pale_yellow.matches([89, -2, 20])
    assert not pale_yellow.matches([90, 0, 1])     # white
    assert not pale_yellow.matches([88, 12, 14])   # pale orange/pink: same chroma, other hue
    assert nearest_colour_name([91, 0, 1], [pale_yellow, white])[0] == "white"


def test_from_samples_tolerance_covers_sample_variation():
    rng = np.random.default_rng(1)
    samples = [rng.normal([45, 55, 35], 2.0, (300, 3)) + [k, 0, 0] for k in (-3, 0, 3)]
    ref = ColourReference.from_samples("red", samples)
    assert not ref.achromatic
    for k in (-3, 0, 3):
        assert ref.matches([45 + k, 55, 35])
    assert not ref.matches([60, 35, 60])  # orange


def test_reference_roundtrip():
    ref = ColourReference("blue", [38, 18, -58], tol=11)
    again = ColourReference.from_dict(ref.to_dict())
    assert again.distance([40, 15, -55]) == pytest.approx(ref.distance([40, 15, -55]), rel=1e-4)
