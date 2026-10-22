import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cable_inspection import synthetic  # noqa: E402
from cable_inspection.calibration import TeachSession  # noqa: E402
from cable_inspection.colors import ColourReference  # noqa: E402
from cable_inspection.presets import DEFAULT_PRESETS  # noqa: E402
from cable_inspection.recipes import Recipe  # noqa: E402


class MemoryPresets:
    """Preset store that never touches the config folder."""

    def __init__(self):
        self._refs = {r.name: ColourReference.from_dict(r.to_dict()) for r in DEFAULT_PRESETS}

    def names(self):
        return list(self._refs)

    def get(self, name):
        return self._refs.get(name)

    def all(self):
        return list(self._refs.values())

    def put(self, ref):
        self._refs[ref.name] = ref


@pytest.fixture
def presets():
    return MemoryPresets()


def make_recipe(**kw) -> Recipe:
    s = synthetic.make()
    return Recipe("BLK-3xWHT", roi=s.roi, inspection_region=s.inspection_region,
                  image_size=(s.image.shape[1], s.image.shape[0]), **kw)


@pytest.fixture(scope="session")
def taught_recipe() -> Recipe:
    """Recipe taught from calibration samples (seeds 1-4), separate from evaluation seeds."""
    r = make_recipe()
    t = TeachSession()
    for seed in range(1, 5):
        ok, msg = t.add_auto(synthetic.make(seed=seed, angle_deg=seed - 2.5).image, r)
        assert ok, msg
    t.build(r)
    return r
