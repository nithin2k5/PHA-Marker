"""Teach, colour preset and settings dialogs."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from ..calibration import BODY, STRIPE, TeachSession, preset_from_patches
from ..colors import ColourReference
from ..geometry import clamp_rect, crop
from .widgets import ImageCanvas

PATCH_COLOURS = {BODY: "#00e5ff", STRIPE: "#ff4fd8"}


def _zoom_rect(recipe, frame) -> tuple[int, int, int, int]:
    """Region shown for teaching: the ROI with a margin, or the whole frame."""
    h, w = frame.shape[:2]
    if recipe.roi is None:
        return 0, 0, w, h
    x, y, rw, rh = recipe.roi
    m = int(max(rw, rh) * 0.08)
    return clamp_rect((x - m, y - m, rw + 2 * m, rh + 2 * m), w, h)


class TeachDialog(tk.Toplevel):
    """Teach from good sample: drag patches on body/stripes or take auto samples."""

    def __init__(self, master, recipe, get_frame, on_apply):
        super().__init__(master)
        self.title(f"Teach colours - {recipe.name}")
        self.geometry("1100x640")
        self.recipe = recipe
        self.get_frame = get_frame
        self.on_apply = on_apply
        self.session = TeachSession()
        self.frame = None
        self.patches: list[tuple[tuple, str]] = []
        self.kind = tk.StringVar(value=BODY)
        self.tol_scale = tk.DoubleVar(value=1.0)

        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        self.canvas = ImageCanvas(left)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<<CanvasResized>>", lambda e: self._redraw())
        ttk.Label(left, text="Drag rectangles over CLEAN areas only (no print, glare or edges). "
                             "Grab several frames/cables for robust tolerances.").pack(anchor="w")

        right = ttk.Frame(self, width=330)
        right.pack(side="right", fill="y", padx=6, pady=6)
        right.pack_propagate(False)
        ttk.Label(right, text=f"Body colour: {recipe.body_colour}\nStripe colour: {recipe.stripe_colour}\n"
                              f"Stripe count: {recipe.stripe_count}", justify="left").pack(anchor="w")
        box = ttk.LabelFrame(right, text="Patch type")
        box.pack(fill="x", pady=6)
        ttk.Radiobutton(box, text="Cable body", variable=self.kind, value=BODY).pack(anchor="w")
        ttk.Radiobutton(box, text="Stripe", variable=self.kind, value=STRIPE).pack(anchor="w")
        ttk.Button(right, text="Grab new frame", command=self.grab).pack(fill="x", pady=2)
        ttk.Button(right, text="Auto sample (detector, current frame)", command=self.auto).pack(fill="x", pady=2)
        ttk.Button(right, text="Clear all samples", command=self.clear).pack(fill="x", pady=2)
        ttk.Label(right, text="Tolerance scale").pack(anchor="w", pady=(8, 0))
        ttk.Scale(right, from_=0.6, to=2.0, variable=self.tol_scale, orient="horizontal").pack(fill="x")
        self.tol_lbl = ttk.Label(right, text="1.00")
        self.tol_lbl.pack(anchor="w")
        self.tol_scale.trace_add("write", lambda *a: self.tol_lbl.config(text=f"{self.tol_scale.get():.2f}"))
        self.count_lbl = ttk.Label(right, text="")
        self.count_lbl.pack(anchor="w", pady=4)
        self.log = tk.Text(right, height=14, width=40, wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        ttk.Button(right, text="Apply to recipe", command=self.apply).pack(fill="x", pady=(6, 2))
        ttk.Button(right, text="Close", command=self.destroy).pack(fill="x")
        self.after(100, self.grab)
        self.transient(master)

    def _update_counts(self):
        b, s = self.session.counts
        self.count_lbl.config(text=f"Samples: body {b}, stripe {s}")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(self.session.log[-40:]))
        self.log.see("end")

    def grab(self):
        f = self.get_frame()
        if f is None:
            messagebox.showwarning("Teach", "No camera frame available.", parent=self)
            return
        self.frame = f
        self.patches = []
        self._redraw()
        self._start_select()

    def _start_select(self):
        self.canvas.start_selection(self._on_patch, PATCH_COLOURS[self.kind.get()])

    def _redraw(self):
        if self.frame is None:
            return
        z = _zoom_rect(self.recipe, self.frame)
        self.canvas.crop_offset = (z[0], z[1])
        self.canvas.show(crop(self.frame, z))
        self.canvas.clear_overlay()
        for rect, kind in self.patches:
            self.canvas.draw_rect(rect, PATCH_COLOURS[kind])

    def _on_patch(self, rect):
        kind = self.kind.get()
        n = self.session.add_patch(self.frame, rect, kind)
        if n:
            self.patches.append((rect, kind))
        self._redraw()
        self._update_counts()
        self._start_select()

    def auto(self):
        f = self.get_frame()
        if f is None:
            return
        if not self.recipe.has_regions:
            messagebox.showwarning("Teach", "Draw ROI and stripe region first.", parent=self)
            return
        self.session.add_auto(f, self.recipe)
        self.frame = f
        self._redraw()
        self._update_counts()

    def clear(self):
        self.session.clear()
        self.patches = []
        self._redraw()
        self._update_counts()

    def apply(self):
        try:
            warnings = self.session.build(self.recipe, self.tol_scale.get())
        except ValueError as exc:
            messagebox.showerror("Teach", str(exc), parent=self)
            return
        msg = "Calibration applied. Press 'Save Recipe' to keep it."
        if warnings:
            msg += "\n\nWarnings:\n- " + "\n- ".join(warnings)
        self.on_apply(self.recipe)
        messagebox.showinfo("Teach", msg, parent=self)


class PresetDialog(tk.Toplevel):
    """View/edit named colour presets and calibrate them from patches on the live image."""

    FIELDS = ("L", "a", "b", "tol", "tol_l", "max_chroma", "hue_tol_deg")

    def __init__(self, master, presets, get_frame, roi, on_change):
        super().__init__(master)
        self.title("Colour presets")
        self.geometry("1000x560")
        self.presets = presets
        self.get_frame = get_frame
        self.roi = roi
        self.on_change = on_change
        self.patches: list[tuple] = []
        self.frame = None

        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=6, pady=6)
        self.listbox = tk.Listbox(left, width=22, height=20, exportselection=False)
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._load_selected())
        ttk.Button(left, text="New preset", command=self.new).pack(fill="x", pady=2)
        ttk.Button(left, text="Delete preset", command=self.delete).pack(fill="x")

        mid = ttk.Frame(self)
        mid.pack(side="left", fill="y", padx=6, pady=6)
        self.vars = {k: tk.StringVar() for k in self.FIELDS}
        self.achromatic = tk.BooleanVar()
        for i, k in enumerate(self.FIELDS):
            ttk.Label(mid, text=k).grid(row=i, column=0, sticky="w")
            ttk.Entry(mid, textvariable=self.vars[k], width=10).grid(row=i, column=1, pady=1)
        ttk.Checkbutton(mid, text="achromatic (black/grey/white)", variable=self.achromatic).grid(
            row=len(self.FIELDS), column=0, columnspan=2, sticky="w")
        self.swatch = tk.Label(mid, width=12, height=2, relief="solid", bd=1)
        self.swatch.grid(row=len(self.FIELDS) + 1, column=0, columnspan=2, pady=6)
        ttk.Button(mid, text="Save values", command=self.save_values).grid(
            row=len(self.FIELDS) + 2, column=0, columnspan=2, sticky="ew")
        ttk.Label(mid, text="\nCalibrate from image:\n1. Grab frame\n2. Drag patch(es)\n3. Calibrate",
                  justify="left").grid(row=len(self.FIELDS) + 3, column=0, columnspan=2, sticky="w")
        ttk.Button(mid, text="Grab frame", command=self.grab).grid(
            row=len(self.FIELDS) + 4, column=0, columnspan=2, sticky="ew")
        ttk.Button(mid, text="Calibrate selected from patches", command=self.calibrate).grid(
            row=len(self.FIELDS) + 5, column=0, columnspan=2, sticky="ew", pady=2)

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        self.canvas = ImageCanvas(right)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<<CanvasResized>>", lambda e: self._redraw())
        self._refresh_list()
        self.transient(master)

    def _refresh_list(self, select: str | None = None):
        self.listbox.delete(0, "end")
        for n in self.presets.names():
            self.listbox.insert("end", n)
        names = self.presets.names()
        if select in names:
            i = names.index(select)
            self.listbox.selection_set(i)
            self._load_selected()

    def _selected(self) -> ColourReference | None:
        sel = self.listbox.curselection()
        return self.presets.get(self.listbox.get(sel[0])) if sel else None

    def _load_selected(self):
        r = self._selected()
        if r is None:
            return
        d = r.to_dict()
        for k, v in zip(("L", "a", "b"), d["lab"]):
            self.vars[k].set(f"{v:.1f}")
        for k in ("tol", "tol_l", "max_chroma"):
            self.vars[k].set(f"{d[k]:.1f}")
        self.vars["hue_tol_deg"].set("" if d["hue_tol_deg"] is None else f"{d['hue_tol_deg']:.0f}")
        self.achromatic.set(d["achromatic"])
        b, g, rr = r.display_bgr()
        self.swatch.config(bg=f"#{rr:02x}{g:02x}{b:02x}")

    def save_values(self):
        r = self._selected()
        if r is None:
            return
        try:
            hue = self.vars["hue_tol_deg"].get().strip()
            ref = ColourReference(r.name, [float(self.vars[k].get()) for k in ("L", "a", "b")],
                                  tol=float(self.vars["tol"].get()), achromatic=self.achromatic.get(),
                                  tol_l=float(self.vars["tol_l"].get()),
                                  max_chroma=float(self.vars["max_chroma"].get()),
                                  hue_tol_deg=float(hue) if hue else None)
        except ValueError:
            messagebox.showerror("Presets", "Invalid number", parent=self)
            return
        self.presets.put(ref)
        self.presets.save()
        self.on_change()
        self._refresh_list(r.name)

    def new(self):
        name = simpledialog.askstring("New preset", "Colour name:", parent=self)
        if not name:
            return
        name = name.strip().lower()
        if self.presets.get(name):
            messagebox.showerror("Presets", "Name already exists", parent=self)
            return
        self.presets.put(ColourReference(name, [50, 0, 0]))
        self.presets.save()
        self.on_change()
        self._refresh_list(name)

    def delete(self):
        r = self._selected()
        if r and messagebox.askyesno("Presets", f"Delete preset '{r.name}'?", parent=self):
            self.presets.remove(r.name)
            self.presets.save()
            self.on_change()
            self._refresh_list()

    def grab(self):
        self.frame = self.get_frame()
        self.patches = []
        self._redraw()
        if self.frame is not None:
            self.canvas.start_selection(self._on_patch)

    def _redraw(self):
        if self.frame is None:
            return
        h, w = self.frame.shape[:2]
        z = clamp_rect(self.roi, w, h) if self.roi else (0, 0, w, h)
        self.canvas.crop_offset = (z[0], z[1])
        self.canvas.show(crop(self.frame, z))
        self.canvas.clear_overlay()
        for p in self.patches:
            self.canvas.draw_rect(p, "#00e5ff")

    def _on_patch(self, rect):
        self.patches.append(rect)
        self._redraw()

    def calibrate(self):
        r = self._selected()
        if r is None or self.frame is None or not self.patches:
            messagebox.showwarning("Presets", "Select a preset, grab a frame and drag patches.", parent=self)
            return
        try:
            ref = preset_from_patches(r.name, self.frame, self.patches)
        except ValueError as exc:
            messagebox.showerror("Presets", str(exc), parent=self)
            return
        self.presets.put(ref)
        self.presets.save()
        self.on_change()
        self.patches = []
        self._redraw()
        self._refresh_list(r.name)


def _parse(text: str, old):
    t = text.strip()
    if t.lower() in ("none", ""):
        return None
    if t.lower() in ("true", "yes", "on"):
        return True
    if t.lower() in ("false", "no", "off"):
        return False
    if isinstance(old, int) and not isinstance(old, bool):
        return int(float(t))
    return float(t)


class SettingsDialog(tk.Toplevel):
    """Edit detection thresholds, geometry checks, cycle and camera settings of a recipe."""

    SECTIONS = ("detection", "geometry", "cycle", "camera")

    def __init__(self, master, recipe, on_apply):
        super().__init__(master)
        self.title(f"Recipe settings - {recipe.name}")
        self.geometry("520x620")
        self.recipe = recipe
        self.on_apply = on_apply
        self.vars: dict[tuple[str, str], tk.StringVar] = {}
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        for sec in self.SECTIONS:
            frame = ttk.Frame(nb)
            nb.add(frame, text=sec.capitalize())
            canvas = tk.Canvas(frame, highlightthickness=0)
            sb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
            inner = ttk.Frame(canvas)
            inner.bind("<Configure>", lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.configure(yscrollcommand=sb.set)
            canvas.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            for i, (k, v) in enumerate(getattr(recipe, sec).items()):
                ttk.Label(inner, text=k).grid(row=i, column=0, sticky="w", padx=4, pady=1)
                var = tk.StringVar(value="" if v is None else str(v))
                ttk.Entry(inner, textvariable=var, width=16).grid(row=i, column=1, padx=4)
                self.vars[(sec, k)] = var
        note = ttk.Frame(self)
        note.pack(fill="x", padx=6)
        ttk.Label(note, text="mm per pixel needs a calibrated scale (e.g. measure a known stripe "
                              "pitch).\nCamera settings take effect when the camera is restarted.",
                  foreground="#555").pack(anchor="w")
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=6, pady=6)
        ttk.Button(btns, text="Apply", command=self.apply).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        self.transient(master)

    def apply(self):
        new = {sec: dict(getattr(self.recipe, sec)) for sec in self.SECTIONS}
        try:
            for (sec, k), var in self.vars.items():
                new[sec][k] = _parse(var.get(), new[sec][k])
        except ValueError as exc:
            messagebox.showerror("Settings", f"Invalid value: {exc}", parent=self)
            return
        for sec in self.SECTIONS:
            setattr(self.recipe, sec, new[sec])
        self.on_apply(self.recipe)
        self.destroy()
