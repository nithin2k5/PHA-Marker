"""Teach a recipe from known-good image files (offline equivalent of "Teach Colours").

    python tools/teach_from_images.py --name BLK-3xGRN --base EXAMPLE-BLK-3xGRY \
        --body black --stripe green --count 3 --calibrate-presets good_green_*.png

* Regions, thresholds and camera settings are copied from ``--base``
  (the cable must sit in the same fixture position).
* Each image is one auto sample (the detector finds body and stripes); use
  several images of different cables for realistic tolerances.
* ``--calibrate-presets`` also sets the named presets for the body and stripe
  colours from the same pixels, so observed colours are named correctly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marker_engine.calibration import TeachSession  # noqa: E402
from marker_engine.colors import ColourReference  # noqa: E402
from marker_engine.presets import PresetStore  # noqa: E402
from marker_engine.recipes import RecipeStore  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--base", required=True, help="recipe providing regions and settings")
    ap.add_argument("--body", required=True)
    ap.add_argument("--stripe", required=True)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--tol-scale", type=float, default=1.0)
    ap.add_argument("--calibrate-presets", action="store_true")
    args = ap.parse_args(argv)

    store = RecipeStore()
    base = store.get(args.base)
    if base is None:
        ap.error(f"unknown base recipe {args.base!r}")
    recipe = store.get(args.name) or base.copy()
    recipe.name = args.name
    recipe.body_colour, recipe.stripe_colour, recipe.stripe_count = args.body, args.stripe, args.count
    recipe.body_ref = recipe.stripe_ref = None
    recipe.cable_width_px = None   # re-learnt from these images
    recipe.notes = f"taught from {len(args.images)} image(s) with tools/teach_from_images.py"

    session = TeachSession()
    for p in args.images:
        img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            print(f"  {p.name}: cannot read")
            continue
        ok, msg = session.add_auto(img, recipe)
        print(f"  {p.name}: {msg}")
    try:
        warnings = session.build(recipe, args.tol_scale)
    except ValueError as exc:
        print("teaching failed:", exc)
        return 1
    for w in warnings:
        print("  warning:", w)
    saved = store.save(recipe)
    print(f"saved {saved.name} v{saved.version}: body {saved.body_ref['lab']}, stripe {saved.stripe_ref['lab']}")

    if args.calibrate_presets:
        presets = PresetStore()
        for name, samples in ((args.body, session.body_samples), (args.stripe, session.stripe_samples)):
            presets.put(ColourReference.from_samples(name, samples))
            print(f"  preset '{name}' calibrated")
        presets.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
