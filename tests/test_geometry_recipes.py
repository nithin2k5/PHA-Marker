from cable_inspection.geometry import (ViewTransform, adapt_rect_to_image, clamp_rect, normalize_rect,
                                      rect_contains)
from cable_inspection.recipes import Recipe, RecipeStore


def test_letterbox_mapping_roundtrip():
    t = ViewTransform(1920, 1080, 800, 600)  # wide image in a 4:3 canvas -> bars top/bottom
    assert t.offset == (0, 75)
    rect = (100, 200, 300, 150)
    assert t.view_rect_to_image(*t.image_rect_to_view(rect)) == rect


def test_selection_outside_image_is_clamped_and_drag_direction_ignored():
    t = ViewTransform(1280, 720, 640, 480)
    # drag from bottom-right to top-left, starting in the letterbox bar
    rect = t.view_rect_to_image(600, 470, 100, 100)
    x, y, w, h = rect
    assert x >= 0 and y >= 0 and x + w <= 1280 and y + h <= 720
    assert normalize_rect(10, 20, 5, 2) == (5, 2, 5, 18)


def test_rect_adapts_to_resolution_change():
    assert adapt_rect_to_image((100, 50, 200, 100), (1280, 720), 640, 360) == (50, 25, 100, 50)
    assert clamp_rect((-10, -10, 50, 50), 100, 100) == (0, 0, 40, 40)
    assert rect_contains((0, 0, 100, 100), (10, 10, 20, 20))


def test_recipe_store_versions_and_defaults(tmp_path):
    store = RecipeStore(tmp_path / "recipes.json")
    r = Recipe("BLU-2xWHT", body_colour="blue", stripe_count=2, roi=[1, 2, 3, 4])
    v1 = store.save(r)
    v2 = store.save(v1)
    assert (v1.version, v2.version) == (1, 2)
    loaded = RecipeStore(tmp_path / "recipes.json").get("BLU-2xWHT")
    assert loaded.roi == (1, 2, 3, 4)
    assert loaded.detection["min_coverage"] > 0      # defaults merged in
    assert loaded.stripe_count == 2


def test_body_reference_prefers_taught_colour(presets):
    r = Recipe("x", body_colour="black", body_ref={"name": "black", "lab": [20, 0, 0], "tol_l": 10})
    assert r.body_reference(presets).lab[0] == 20
    r.body_colour = "blue"  # taught reference no longer applies to the new colour
    assert r.body_reference(presets).name == "blue"
