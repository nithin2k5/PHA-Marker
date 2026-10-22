"""Measure false accepts, false rejects and indeterminate results.

Dataset layout (one folder per expected outcome)::

    dataset/
      pass/       good cables of the recipe's type
      fail/       wrong colour, missing/extra stripe, other cable types...
      no_cable/   empty fixture

Calibration images must be kept separate from evaluation images::

    python tools/evaluate.py --recipe BLK-3xWHT --teach calib/good --data dataset
    python tools/evaluate.py --synthetic            # self-check on generated scenes

Definitions (per image, single-frame verdict):
  false accept  = expected fail/no_cable but verdict PASS   (the critical error)
  false reject  = expected pass but verdict FAIL
  indeterminate = INDETERMINATE (needs operator), counted separately per class
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marker_engine import synthetic  # noqa: E402
from marker_engine.calibration import TeachSession  # noqa: E402
from marker_engine.detection import SetupError, detect  # noqa: E402
from marker_engine.evaluation import FAIL, INDETERMINATE, NO_CABLE, PASS, evaluate  # noqa: E402
from marker_engine.presets import PresetStore  # noqa: E402
from marker_engine.recipes import Recipe, RecipeStore  # noqa: E402

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
CLASSES = ("pass", "fail", "no_cable")


def read_image(path: Path) -> np.ndarray | None:
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def iter_images(folder: Path):
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() in IMAGE_EXTS:
            img = read_image(p)
            if img is not None:
                yield p.name, img


def teach(recipe: Recipe, images) -> None:
    session = TeachSession()
    used = 0
    for name, img in images:
        ok, msg = session.add_auto(img, recipe)
        used += ok
        print(f"  teach {name}: {msg}")
    if not used:
        raise SystemExit("no usable calibration image")
    for w in session.build(recipe):
        print("  warning:", w)


def evaluate_set(recipe: Recipe, presets, samples) -> dict:
    """samples: iterable of (expected_class, name, image)."""
    table: dict[str, Counter] = defaultdict(Counter)
    errors = []
    for expected, name, img in samples:
        try:
            v = evaluate(detect(img, recipe), recipe, presets)
            status = v.status
            reason = v.reasons[0] if v.reasons else ""
        except SetupError as exc:
            status, reason = INDETERMINATE, f"setup: {exc}"
        table[expected][status] += 1
        is_fa = expected != "pass" and status == PASS
        is_fr = expected == "pass" and status == FAIL
        if is_fa or is_fr:
            errors.append({"image": name, "expected": expected, "verdict": status, "reason": reason,
                           "error": "FALSE ACCEPT" if is_fa else "false reject"})
    return {"table": {k: dict(v) for k, v in table.items()}, "errors": errors}


def summarise(result: dict) -> None:
    table = result["table"]
    statuses = (PASS, FAIL, INDETERMINATE, NO_CABLE)
    print("\n expected \\ verdict " + "".join(f"{s:>15}" for s in statuses) + f"{'n':>6}")
    for cls in CLASSES:
        row = table.get(cls, {})
        n = sum(row.values())
        if n:
            print(f" {cls:<18}" + "".join(f"{row.get(s, 0):>15}" for s in statuses) + f"{n:>6}")
    good = table.get("pass", {})
    bad = Counter(table.get("fail", {})) + Counter(table.get("no_cable", {}))
    n_good, n_bad = sum(good.values()), sum(bad.values())
    if n_good:
        print(f"\n false reject rate : {good.get(FAIL, 0) / n_good:6.1%}  ({good.get(FAIL, 0)}/{n_good} good cables)")
        print(f" good -> INDETERM. : {good.get(INDETERMINATE, 0) / n_good:6.1%}")
    if n_bad:
        print(f" FALSE ACCEPT rate : {bad.get(PASS, 0) / n_bad:6.1%}  ({bad.get(PASS, 0)}/{n_bad} bad/empty)")
        print(f" bad -> INDETERM.  : {bad.get(INDETERMINATE, 0) / n_bad:6.1%}")
    for e in result["errors"]:
        print(f"   {e['error']:<13} {e['image']}: expected {e['expected']}, got {e['verdict']} ({e['reason']})")
    if n_good + n_bad < 200:
        print("\n NOTE: small sample - rates are not statistically meaningful. Validate with hundreds of "
              "real cables per class (see README).")


def synthetic_samples(seed0: int = 1000, n: int = 10):
    rng = np.random.default_rng(seed0)
    defects = [{"stripe": "red"}, {"body": "blue"}, {"stripe_count": 2}, {"stripe_count": 4},
               {"stripe_colours": ["white", "yellow", "white"]}, {"body": "grey"}]
    for i in range(n):
        common = {"seed": seed0 + i, "angle_deg": float(rng.uniform(-6, 6)),
                  "shift": (int(rng.integers(-20, 20)), int(rng.integers(-8, 8))),
                  "text_in_region": bool(rng.integers(0, 2)), "glare": float(rng.uniform(0, 0.4))}
        yield "pass", f"good_{i}", synthetic.make(**common).image
        yield "fail", f"defect_{i}", synthetic.make(**{**common, **defects[i % len(defects)]}).image
        yield "no_cable", f"empty_{i}", synthetic.make(seed=seed0 + i, no_cable=True).image


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recipe", help="recipe name from config/recipes.json")
    ap.add_argument("--teach", type=Path, help="folder of GOOD calibration images (not used for evaluation)")
    ap.add_argument("--data", type=Path, help="evaluation folder with pass/ fail/ no_cable/ subfolders")
    ap.add_argument("--synthetic", action="store_true", help="run on generated scenes instead")
    ap.add_argument("--json", type=Path, help="write the result table to this JSON file")
    args = ap.parse_args(argv)
    presets = PresetStore()

    if args.synthetic:
        base = synthetic.make()
        recipe = Recipe("SYNTH-BLK-3xWHT", roi=base.roi, inspection_region=base.inspection_region,
                        image_size=(base.image.shape[1], base.image.shape[0]))
        print("teaching on calibration seeds 1-5 ...")
        teach(recipe, ((f"calib_{s}", synthetic.make(seed=s, angle_deg=s - 3).image) for s in range(1, 6)))
        result = evaluate_set(recipe, presets, synthetic_samples())
    else:
        if not (args.recipe and args.data):
            ap.error("--recipe and --data are required (or use --synthetic)")
        recipe = RecipeStore().get(args.recipe)
        if recipe is None:
            ap.error(f"unknown recipe {args.recipe!r}")
        if args.teach:
            overlap = {p.name for p in args.teach.rglob("*")} & {p.name for p in args.data.rglob("*")}
            if overlap:
                print(f"WARNING: {len(overlap)} file names appear in both calibration and evaluation sets")
            print(f"teaching from {args.teach} ...")
            teach(recipe, iter_images(args.teach))
        samples = ((cls, name, img) for cls in CLASSES if (args.data / cls).exists()
                   for name, img in iter_images(args.data / cls))
        result = evaluate_set(recipe, presets, samples)
    summarise(result)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
