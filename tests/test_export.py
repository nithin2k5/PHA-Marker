import json

from marker_engine import synthetic
from marker_engine.cycle import FrameResult, InspectionCycle
from marker_engine.detection import detect
from marker_engine.evaluation import evaluate
from marker_engine.export import export_result


def test_export_writes_summary_and_images(tmp_path, taught_recipe, presets):
    img = synthetic.make(seed=20, stripe_count=2).image
    cycle = InspectionCycle({"frames_required": 2})
    cycle.set_recipe(taught_recipe.name, taught_recipe.version)
    cycle.trigger(0.0, 0)
    res = None
    for seq in (1, 2):
        m = detect(img, taught_recipe)
        res = cycle.on_frame(FrameResult(seq, seq, m, evaluate(m, taught_recipe, presets), img), seq * 0.1) or res
    assert res is not None and res.status == "FAIL"
    files = export_result(res, tmp_path, taught_recipe)
    names = {p.suffix for p in files}
    assert {".json", ".png"} <= names
    data = json.loads(next(p for p in files if p.suffix == ".json").read_text())
    assert data["status"] == "FAIL"
    assert data["expected"]["stripe_count"] == 3
    assert data["observed"]["stripe_count"] == 2
    assert "missing stripe" in data["reasons"][0]
    assert data["cycle_id"] == res.cycle_id
