"""Write synthetic development images (and a short video) to samples/synthetic/.

    python tools/make_samples.py

Open them in the app with "Open Image/Video".  The DEMO recipe in
config/recipes.json uses the matching regions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marker_engine import synthetic  # noqa: E402
from marker_engine.config import SAMPLES_DIR  # noqa: E402

SCENES = {
    "good_01": {"seed": 31},
    "good_rotated_print": {"seed": 32, "angle_deg": 4, "text_in_region": True},
    "good_shifted": {"seed": 33, "shift": (20, 6), "angle_deg": -3},
    "fail_red_stripes": {"seed": 34, "stripe": "red"},
    "fail_blue_body": {"seed": 35, "body": "blue"},
    "fail_missing_stripe": {"seed": 36, "stripe_count": 2},
    "fail_extra_stripe": {"seed": 37, "stripe_count": 4},
    "fail_one_yellow_stripe": {"seed": 38, "stripe_colours": ["white", "yellow", "white"]},
    "indet_glare": {"seed": 39, "glare": 1.0},
    "indet_obstructed": {"seed": 40, "occlusion": True},
    "indet_blurred": {"seed": 41, "blur": 5.0},
    "no_cable": {"seed": 42, "no_cable": True},
}


def write_video(path: Path, fps: int = 10) -> None:
    """Cable placed, held, removed, next (defective) cable placed: exercises automatic mode."""
    frames = ([synthetic.make(seed=50, no_cable=True).image] * 15
              + [synthetic.make(seed=51).image] * 25
              + [synthetic.make(seed=52, no_cable=True).image] * 15
              + [synthetic.make(seed=53, stripe_count=2).image] * 25
              + [synthetic.make(seed=54, no_cable=True).image] * 10)
    h, w = frames[0].shape[:2]
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    rng = np.random.default_rng(0)
    for f in frames:
        noisy = np.clip(f.astype(np.int16) + rng.integers(-2, 3, f.shape), 0, 255).astype(np.uint8)
        vw.write(noisy)
    vw.release()


def main() -> None:
    out = SAMPLES_DIR / "synthetic"
    out.mkdir(parents=True, exist_ok=True)
    for name, kw in SCENES.items():
        cv2.imwrite(str(out / f"{name}.png"), synthetic.make(**kw).image)
        print("wrote", name)
    write_video(out / "auto_mode_sequence.mp4")
    print("wrote auto_mode_sequence.mp4 ->", out)


if __name__ == "__main__":
    main()
