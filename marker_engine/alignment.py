"""Fixture alignment: follow the regions when the camera framing changes.

At teach time a small edge-image template of the fixture around the cable ROI
is stored in the recipe.  At inspection time the template is searched in the
new frame over translation and scale (multi-scale normalised cross-correlation
on gradient images, so it is insensitive to brightness), and the ROI / stripe
region are mapped through the found shift and scale.

This compensates moderate re-framing (camera nudged, different crop or
resolution, zoom within ``SCALES``).  It does not handle rotation of the
camera by more than a few degrees or a different fixture; then redraw the
regions and teach again.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import cv2
import numpy as np

from .geometry import adapt_rect_to_image, clamp_rect, crop

TEMPLATE_WIDTH = 320                      # template width in pixels (after downscaling)
SCALES = np.arange(0.5, 1.61, 0.05)       # scene scale search range, relative to the teach frame

_cache: dict[tuple, tuple[float, float]] = {}  # (template id, frame size) -> (last scale, last score)


@dataclass
class Alignment:
    scale: float          # new-frame pixels per teach-frame pixel
    dx: float             # translation of the teach-frame origin, new-frame pixels
    dy: float
    score: float          # normalised correlation of the best match (0..1)
    teach_size: tuple[int, int]

    def map_point(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale + self.dx, y * self.scale + self.dy

    def map_rect(self, rect) -> tuple[int, int, int, int]:
        x, y, w, h = rect
        nx, ny = self.map_point(x, y)
        return int(round(nx)), int(round(ny)), int(round(w * self.scale)), int(round(h * self.scale))


def _features(bgr: np.ndarray) -> np.ndarray:
    grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    grey = cv2.GaussianBlur(grey, (0, 0), 1.5)
    gx = cv2.Sobel(grey, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(grey, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    return cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)


def make_template(frame: np.ndarray, recipe) -> dict:
    """Template of the fixture around the ROI of a (good) teach frame."""
    ih, iw = frame.shape[:2]
    x, y, w, h = adapt_rect_to_image(recipe.roi, recipe.image_size, iw, ih)
    mx, my = int(0.1 * w), int(max(0.8 * h, 40))
    rect = clamp_rect((x - mx, y - my, w + 2 * mx, h + 2 * my), iw, ih)
    factor = TEMPLATE_WIDTH / max(rect[2], 1)
    small = cv2.resize(_features(crop(frame, rect)), None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", small)
    return {"png": base64.b64encode(buf.tobytes()).decode("ascii"), "rect": [int(v) for v in rect],
            "factor": factor, "teach_size": [iw, ih]}


def _decode(t: dict) -> np.ndarray:
    data = np.frombuffer(base64.b64decode(t["png"]), np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)


def locate(frame: np.ndarray, t: dict) -> Alignment:
    """Find the teach-time fixture template in ``frame``."""
    tmpl = _decode(t)
    factor = float(t["factor"])
    rx, ry = t["rect"][0], t["rect"][1]
    feat = _features(frame)
    small = cv2.resize(feat, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    key = (t["png"][:64], frame.shape[:2])

    def try_scale(s):
        th, tw = int(round(tmpl.shape[0] * s)), int(round(tmpl.shape[1] * s))
        if th < 8 or tw < 16 or th > sh or tw > sw:
            return None
        ts = cv2.resize(tmpl, (tw, th), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        res = cv2.matchTemplate(small, ts, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        return float(score), float(s), loc

    best = None
    prev = _cache.get(key)
    if prev is not None:
        # fixed camera: re-check around the last scale first; accept if the
        # match is about as good as last time
        for s in (prev[0] - 0.02, prev[0], prev[0] + 0.02):
            r = try_scale(s)
            if r and (best is None or r[0] > best[0]):
                best = r
    if best is None or best[0] < 0.9 * (prev[1] if prev else 1.0) or best[0] < 0.3:
        for s in SCALES:
            r = try_scale(s)
            if r and (best is None or r[0] > best[0]):
                best = r
        if best is not None:
            for s in np.arange(best[1] - 0.04, best[1] + 0.041, 0.01):
                r = try_scale(s)
                if r and r[0] > best[0]:
                    best = r
    if best is None:
        return Alignment(1.0, 0.0, 0.0, 0.0, tuple(t["teach_size"]))
    score, s, (lx, ly) = best
    _cache[key] = (s, score)
    # template pixel (0,0) <-> teach-frame point (rx, ry); found at (lx, ly) in `small`
    dx = lx / factor - rx * s
    dy = ly / factor - ry * s
    return Alignment(s, dx, dy, score, tuple(t["teach_size"]))


def aligned_regions(frame: np.ndarray, recipe) -> tuple[tuple, tuple, Alignment] | None:
    """ROI and stripe region mapped into ``frame`` coordinates, or None without a template."""
    t = getattr(recipe, "fixture_template", None)
    if not t:
        return None
    a = locate(frame, t)
    tw, th = a.teach_size
    roi_t = adapt_rect_to_image(recipe.roi, recipe.image_size, tw, th)
    reg_t = adapt_rect_to_image(recipe.inspection_region, recipe.image_size, tw, th)
    ih, iw = frame.shape[:2]
    return (clamp_rect(a.map_rect(roi_t), iw, ih), clamp_rect(a.map_rect(reg_t), iw, ih), a)
