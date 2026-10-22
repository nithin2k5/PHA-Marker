"""Colour space conversion and perceptual colour differences.

All colour comparison is done in CIE L*a*b* (L 0..100, a/b roughly -128..127),
never on raw RGB values or hue alone.  Lab separates lightness from chroma so
black/grey/white cables (which have no meaningful hue) can be handled
separately from chromatic colours.
"""

from __future__ import annotations

import cv2
import numpy as np


def bgr_to_lab(bgr: np.ndarray) -> np.ndarray:
    """Convert a uint8 BGR image (or Nx3 pixel array) to float32 Lab."""
    arr = np.asarray(bgr)
    shape = arr.shape
    img = arr.reshape(-1, 1, 3) if arr.ndim == 2 else arr
    lab = cv2.cvtColor(img.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)
    return lab.reshape(shape).astype(np.float32)


def lab_to_bgr(lab) -> tuple[int, int, int]:
    """Convert a single Lab triple to a displayable BGR uint8 tuple."""
    px = np.array(lab, dtype=np.float32).reshape(1, 1, 3)
    bgr = cv2.cvtColor(px, cv2.COLOR_LAB2BGR)[0, 0]
    return tuple(int(v) for v in np.clip(bgr * 255.0 + 0.5, 0, 255))


def chroma(lab: np.ndarray) -> np.ndarray:
    lab = np.asarray(lab, dtype=np.float32)
    return np.hypot(lab[..., 1], lab[..., 2])


def hue_deg(lab: np.ndarray) -> np.ndarray:
    lab = np.asarray(lab, dtype=np.float32)
    return np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360.0


def delta_e_2000(lab1, lab2) -> np.ndarray:
    """Vectorised CIEDE2000 colour difference.  Inputs broadcast (..., 3)."""
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    Cbar = (C1 + C2) / 2.0
    G = 0.5 * (1 - np.sqrt(Cbar**7 / (Cbar**7 + 25.0**7)))
    a1p = (1 + G) * a1
    a2p = (1 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0

    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, dhp)
    dhp = np.where(dhp < -180, dhp + 360, dhp)
    dhp = np.where(C1p * C2p == 0, 0.0, dhp)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2.0)

    Lbarp = (L1 + L2) / 2.0
    Cbarp = (C1p + C2p) / 2.0
    hsum = h1p + h2p
    hbarp = np.where(np.abs(h1p - h2p) > 180, (hsum + 360) / 2.0, hsum / 2.0)
    hbarp = np.where(C1p * C2p == 0, hsum, hbarp)

    T = (1 - 0.17 * np.cos(np.radians(hbarp - 30))
         + 0.24 * np.cos(np.radians(2 * hbarp))
         + 0.32 * np.cos(np.radians(3 * hbarp + 6))
         - 0.20 * np.cos(np.radians(4 * hbarp - 63)))
    dtheta = 30 * np.exp(-(((hbarp - 275) / 25.0) ** 2))
    Rc = 2 * np.sqrt(Cbarp**7 / (Cbarp**7 + 25.0**7))
    Sl = 1 + (0.015 * (Lbarp - 50) ** 2) / np.sqrt(20 + (Lbarp - 50) ** 2)
    Sc = 1 + 0.045 * Cbarp
    Sh = 1 + 0.015 * Cbarp * T
    Rt = -np.sin(np.radians(2 * dtheta)) * Rc

    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                   + Rt * (dCp / Sc) * (dHp / Sh))


def robust_lab(pixels_lab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Median Lab and per-channel MAD (scaled to sigma) of an Nx3 pixel set."""
    px = np.asarray(pixels_lab, dtype=np.float32).reshape(-1, 3)
    if px.size == 0:
        raise ValueError("no pixels")
    med = np.median(px, axis=0)
    mad = np.median(np.abs(px - med), axis=0) * 1.4826
    return med, mad
