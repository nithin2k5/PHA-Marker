"""Reusable Tk widgets."""

from __future__ import annotations

import tkinter as tk

import cv2
import numpy as np
from PIL import Image, ImageTk

from marker_engine.geometry import ViewTransform


class ImageCanvas(tk.Canvas):
    """Shows a BGR image letterboxed in the canvas and lets the user drag rectangles.

    Selections are reported in ORIGINAL image coordinates (through
    ``ViewTransform``), independent of how the preview is resized.
    An optional ``crop_offset`` lets the canvas show a crop of a larger image
    while still reporting full-image coordinates.
    """

    def __init__(self, master, bg="#202124", **kw):
        super().__init__(master, bg=bg, highlightthickness=0, **kw)
        self._photo = None
        self._img_size: tuple[int, int] | None = None
        self.crop_offset = (0, 0)
        self._drag_start = None
        self._rubber = None
        self._on_select = None
        self._select_colour = "#00e5ff"
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self.event_generate("<<CanvasResized>>"))

    # -- display ------------------------------------------------------------
    @property
    def transform(self) -> ViewTransform | None:
        if self._img_size is None:
            return None
        return ViewTransform(self._img_size[0], self._img_size[1],
                             max(2, self.winfo_width()), max(2, self.winfo_height()))

    def show(self, bgr: np.ndarray | None) -> None:
        if bgr is None:
            return
        h, w = bgr.shape[:2]
        self._img_size = (w, h)
        t = self.transform
        dw, dh = t.disp_size
        interp = cv2.INTER_AREA if dw < w else cv2.INTER_NEAREST
        small = cv2.resize(bgr, (dw, dh), interpolation=interp)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        ox, oy = t.offset
        self.delete("img")
        self.create_image(ox, oy, image=self._photo, anchor="nw", tags="img")
        self.tag_lower("img")

    def draw_rect(self, rect, colour: str, tag: str = "overlay", width: int = 2, dash=None) -> None:
        """Draw a rect given in full-image coordinates."""
        t = self.transform
        if t is None or rect is None:
            return
        x, y, w, h = rect
        x -= self.crop_offset[0]
        y -= self.crop_offset[1]
        a = t.image_to_view(x, y)
        b = t.image_to_view(x + w, y + h)
        self.create_rectangle(a[0], a[1], b[0], b[1], outline=colour, width=width, dash=dash, tags=tag)

    def clear_overlay(self, tag: str = "overlay") -> None:
        self.delete(tag)

    # -- selection ----------------------------------------------------------------
    def start_selection(self, callback, colour: str = "#00e5ff") -> None:
        """Next drag reports a rect (x, y, w, h) in full-image pixels to ``callback``."""
        self._on_select = callback
        self._select_colour = colour
        self.config(cursor="crosshair")

    def cancel_selection(self) -> None:
        self._on_select = None
        self._drag_start = None
        self.delete("rubber")
        self.config(cursor="")

    @property
    def selecting(self) -> bool:
        return self._on_select is not None

    def _press(self, e):
        if self._on_select and self.transform:
            self._drag_start = (e.x, e.y)

    def _motion(self, e):
        if self._drag_start:
            self.delete("rubber")
            x0, y0 = self._drag_start
            self.create_rectangle(x0, y0, e.x, e.y, outline=self._select_colour, width=2,
                                  dash=(4, 2), tags="rubber")

    def _release(self, e):
        if not self._drag_start or not self._on_select:
            return
        x0, y0 = self._drag_start
        self._drag_start = None
        self.delete("rubber")
        rect = self.transform.view_rect_to_image(x0, y0, e.x, e.y)
        rect = (rect[0] + self.crop_offset[0], rect[1] + self.crop_offset[1], rect[2], rect[3])
        cb = self._on_select
        if rect[2] < 4 or rect[3] < 4:
            return  # ignore clicks; stay in selection mode
        cb(rect)


def colour_swatch(master, bgr, size=18) -> tk.Label:
    r, g, b = bgr[2], bgr[1], bgr[0]
    return tk.Label(master, width=2, bg=f"#{r:02x}{g:02x}{b:02x}", relief="solid", bd=1)
