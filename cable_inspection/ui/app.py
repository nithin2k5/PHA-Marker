"""Main window: Cable Colour and Stripe Inspection."""

from __future__ import annotations

import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import APP_NAME, __version__
from ..alerts import AlertSounder
from ..annotate import annotate, magnified_view
from ..camera import CameraWorker, FrameStore, ImageSource, make_source
from ..config import EXPORT_DIR, SAMPLES_DIR
from ..cycle import ACQUIRING, WAIT_CABLE, WAIT_REMOVAL
from ..evaluation import CAMERA_ERROR, FAIL, INDETERMINATE, NO_CABLE, PASS
from ..export import export_result
from ..geometry import rect_contains
from ..presets import PresetStore
from ..processing import InspectionProcessor
from ..recipes import Recipe, RecipeStore
from .. import synthetic
from .dialogs import PresetDialog, SettingsDialog, TeachDialog
from .widgets import ImageCanvas

STATUS_STYLE = {
    PASS: ("#1e8e3e", "white"),
    FAIL: ("#d93025", "white"),
    INDETERMINATE: ("#f29900", "black"),
    NO_CABLE: ("#5f6368", "white"),
    CAMERA_ERROR: ("#8e24aa", "white"),
    "READY": ("#3c4043", "white"),
    "INSPECTING": ("#1a73e8", "white"),
}

DEMO_SCENES = {
    "Good cable (3 white stripes)": {},
    "Good cable, rotated 4 deg + print": {"angle_deg": 4, "text_in_region": True, "seed": 3},
    "Wrong stripe colour (red)": {"stripe": "red"},
    "Wrong body colour (blue)": {"body": "blue"},
    "Missing stripe": {"stripe_count": 2},
    "Extra stripe": {"stripe_count": 4},
    "Glare on stripes": {"glare": 1.0},
    "Obstructed": {"occlusion": True},
    "Blurred": {"blur": 3.0},
    "No cable": {"no_cable": True},
}


class App:
    def __init__(self, root: tk.Tk, recipes: RecipeStore | None = None, presets: PresetStore | None = None):
        self.root = root
        root.title(f"{APP_NAME}  v{__version__}")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{min(1500, sw - 40)}x{min(920, sh - 90)}+10+10")
        root.minsize(1000, 640)
        try:
            root.state("zoomed")  # maximised on Windows
        except tk.TclError:
            pass
        self.recipes = recipes or RecipeStore()
        self.presets = presets or PresetStore()
        self.store = FrameStore()
        self.ui_queue: queue.Queue = queue.Queue()
        self.cam_events: queue.Queue = queue.Queue()
        self.camera: CameraWorker | None = None
        self.processor = InspectionProcessor(self.store, self.presets, self.ui_queue)
        self.recipe: Recipe | None = None
        self.dirty = False
        self.latched = None
        self.live_m = None
        self.live_frame = None
        self.auto_on = False
        self.files: list[str] = []
        self.file_idx = 0
        self.alert_active = False
        self._flash = False
        self.sounder = AlertSounder(fallback=None)

        self._build_styles()
        self._build_toolbar()
        self._build_statusbar()   # packed before the body so it is never squeezed out
        self._build_body()
        self._load_recipe_list()
        self.processor.start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(30, self._poll)
        self.root.after(500, self._flash_tick)

    # ------------------------------------------------------------------ layout
    def _build_styles(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=6)
        st.configure("TButton", padding=4)
        st.configure("Head.TLabel", font=("Segoe UI", 10, "bold"))

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(6, 4))
        bar.pack(side="top", fill="x")
        row1 = ttk.Frame(bar)
        row1.pack(fill="x")
        row2 = ttk.Frame(bar)
        row2.pack(fill="x", pady=(4, 0))

        ttk.Label(row1, text="Camera").pack(side="left")
        self.cam_index = tk.StringVar(value="0")
        ttk.Spinbox(row1, from_=0, to=9, width=3, textvariable=self.cam_index).pack(side="left", padx=2)
        self.btn_cam = ttk.Button(row1, text="Start Camera", command=self.toggle_camera)
        self.btn_cam.pack(side="left", padx=2)
        ttk.Button(row1, text="Open Image/Video", command=self.open_file).pack(side="left", padx=2)
        ttk.Button(row1, text="\u25c0", width=3, command=lambda: self.step_file(-1)).pack(side="left")
        self.file_lbl = ttk.Label(row1, text="", width=18, anchor="center")
        self.file_lbl.pack(side="left")
        ttk.Button(row1, text="\u25b6", width=3, command=lambda: self.step_file(1)).pack(side="left", padx=(0, 2))
        self.root.bind("<Left>", lambda e: self.step_file(-1))
        self.root.bind("<Right>", lambda e: self.step_file(1))
        demo = ttk.Menubutton(row1, text="Demo Scene")
        menu = tk.Menu(demo, tearoff=False)
        for name in DEMO_SCENES:
            menu.add_command(label=name, command=lambda n=name: self.load_demo(n))
        demo["menu"] = menu
        demo.pack(side="left", padx=2)
        ttk.Separator(row1, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(row1, text="Draw ROI", command=lambda: self.draw_region("roi")).pack(side="left", padx=2)
        ttk.Button(row1, text="Draw Stripe Region",
                   command=lambda: self.draw_region("inspection_region")).pack(side="left", padx=2)
        ttk.Button(row1, text="Teach Colours", command=self.teach).pack(side="left", padx=2)
        ttk.Button(row1, text="Save Recipe", command=self.save_recipe).pack(side="left", padx=2)
        ttk.Button(row1, text="New Recipe", command=self.new_recipe).pack(side="left", padx=2)
        ttk.Button(row1, text="Delete Recipe", command=self.delete_recipe).pack(side="left", padx=2)
        ttk.Separator(row1, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(row1, text="Settings", command=self.settings).pack(side="left", padx=2)
        ttk.Button(row1, text="Colour Presets", command=self.edit_presets).pack(side="left", padx=2)

        self.btn_inspect = ttk.Button(row2, text="INSPECT", style="Big.TButton", command=self.inspect)
        self.btn_inspect.pack(side="left", padx=2)
        self.btn_auto = ttk.Button(row2, text="Start Automatic Inspection", style="Big.TButton",
                                   command=self.toggle_auto)
        self.btn_auto.pack(side="left", padx=2)
        self.btn_ack = ttk.Button(row2, text="Acknowledge Alert", style="Big.TButton",
                                  command=self.acknowledge, state="disabled")
        self.btn_ack.pack(side="left", padx=2)
        ttk.Button(row2, text="Export Result", command=self.export).pack(side="left", padx=2)
        self.identify_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Identify type (compare all recipes)", variable=self.identify_var,
                        command=lambda: self.processor.send("identify", self.identify_var.get())
                        ).pack(side="left", padx=10)
        self.sound_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="Audible alert", variable=self.sound_var,
                        command=self._sound_changed).pack(side="left")

    def _build_body(self):
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=6, pady=4)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        self.preview = ImageCanvas(left)
        self.preview.pack(fill="both", expand=True)
        self.preview_hint = ttk.Label(left, text="Start the camera, open an image/video or load a demo scene.")
        self.preview_hint.pack(anchor="w")
        left.grid(row=0, column=0, sticky="nsew")

        # fixed-width side panel; the preview gets all remaining space
        right = ttk.Frame(body, width=460)
        right.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        right.pack_propagate(False)

        # result indicator
        self.result_lbl = tk.Label(right, text="READY", font=("Segoe UI", 28, "bold"),
                                   bg=STATUS_STYLE["READY"][0], fg="white", height=1)
        self.result_lbl.pack(fill="x")
        self.reason_lbl = tk.Label(right, text="Select a recipe, position the cable and press INSPECT.",
                                   font=("Segoe UI", 11), wraplength=420, justify="left", anchor="w",
                                   bg="#f1f3f4", padx=6, pady=4)
        self.reason_lbl.pack(fill="x")

        # recipe panel
        rf = ttk.LabelFrame(right, text="Recipe", padding=6)
        rf.pack(fill="x", pady=6)
        ttk.Label(rf, text="Recipe").grid(row=0, column=0, sticky="w")
        self.recipe_var = tk.StringVar()
        self.recipe_cb = ttk.Combobox(rf, textvariable=self.recipe_var, state="readonly", width=28)
        self.recipe_cb.grid(row=0, column=1, columnspan=2, sticky="ew", pady=2)
        self.recipe_cb.bind("<<ComboboxSelected>>", lambda e: self.select_recipe(self.recipe_var.get()))
        ttk.Label(rf, text="Cable colour").grid(row=1, column=0, sticky="w")
        self.body_var = tk.StringVar()
        self.body_cb = ttk.Combobox(rf, textvariable=self.body_var, state="readonly", width=16)
        self.body_cb.grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(rf, text="Stripe colour").grid(row=2, column=0, sticky="w")
        self.stripe_var = tk.StringVar()
        self.stripe_cb = ttk.Combobox(rf, textvariable=self.stripe_var, state="readonly", width=16)
        self.stripe_cb.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(rf, text="Stripe count").grid(row=3, column=0, sticky="w")
        self.count_var = tk.IntVar(value=3)
        ttk.Spinbox(rf, from_=0, to=12, width=5, textvariable=self.count_var,
                    command=self._recipe_fields_changed).grid(row=3, column=1, sticky="w", pady=2)
        self.body_cb.bind("<<ComboboxSelected>>", lambda e: self._recipe_fields_changed())
        self.stripe_cb.bind("<<ComboboxSelected>>", lambda e: self._recipe_fields_changed())
        self.count_var.trace_add("write", lambda *a: self._recipe_fields_changed())
        self.recipe_info = ttk.Label(rf, text="", foreground="#555", justify="left")
        self.recipe_info.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        rf.columnconfigure(2, weight=1)

        # magnified stripe view
        mf = ttk.LabelFrame(right, text="Stripe region (straightened, enlarged)", padding=4)
        mf.pack(fill="x")
        self.mag = ImageCanvas(mf, height=170)
        self.mag.pack(fill="x")

        # measurements
        ms = ttk.LabelFrame(right, text="Measurements", padding=4)
        ms.pack(fill="both", expand=True, pady=6)
        self.meas_txt = tk.Text(ms, height=8, wrap="word", font=("Consolas", 9), relief="flat")
        self.meas_txt.pack(fill="both", expand=True)
        self.meas_txt.tag_configure("bad", foreground="#d93025")
        self.meas_txt.tag_configure("good", foreground="#1e8e3e")
        self.meas_txt.tag_configure("warn", foreground="#b06000")
        self.meas_txt.tag_configure("head", font=("Consolas", 9, "bold"))

    def _build_statusbar(self):
        sb = ttk.Frame(self.root, padding=(6, 2))
        sb.pack(side="bottom", fill="x")
        self.cam_status = ttk.Label(sb, text="Camera: stopped")
        self.cam_status.pack(side="left")
        self.phase_status = ttk.Label(sb, text="Cycle: IDLE")
        self.phase_status.pack(side="left", padx=20)
        self.live_status = ttk.Label(sb, text="")
        self.live_status.pack(side="left", padx=20)
        self.perf_status = ttk.Label(sb, text="")
        self.perf_status.pack(side="right")

    # ------------------------------------------------------------------ recipes
    def _load_recipe_list(self, select: str | None = None):
        names = self.recipes.names()
        if not names:
            r = Recipe("DEMO-BLK-3xWHT", notes="created automatically")
            self.recipes.save(r)
            names = self.recipes.names()
        self.recipe_cb["values"] = names
        colours = self.presets.names()
        self.body_cb["values"] = colours
        self.stripe_cb["values"] = colours
        target = select if select in names else (self.recipe.name if self.recipe and self.recipe.name in names
                                                 else names[0])
        self.select_recipe(target, force=True)

    def select_recipe(self, name: str, force: bool = False):
        if not force and self.dirty and self.recipe and name != self.recipe.name:
            if not messagebox.askyesno("Unsaved changes",
                                       f"Discard unsaved changes to '{self.recipe.name}'?"):
                self.recipe_var.set(self.recipe.name)
                return
        r = self.recipes.get(name)
        if r is None:
            return
        self.recipe = r
        self.dirty = False
        self._suspend_trace = True
        self.recipe_var.set(r.name)
        self.body_var.set(r.body_colour)
        self.stripe_var.set(r.stripe_colour)
        self.count_var.set(r.stripe_count)
        self._suspend_trace = False
        self._push_recipe()
        self._clear_result("Recipe selected - position the cable and press INSPECT.")

    def _recipe_fields_changed(self):
        if getattr(self, "_suspend_trace", False) or self.recipe is None:
            return
        try:
            count = int(self.count_var.get())
        except (tk.TclError, ValueError):
            return
        r = self.recipe
        changed = (r.body_colour, r.stripe_colour, r.stripe_count) != (self.body_var.get(), self.stripe_var.get(), count)
        if not changed:
            return
        r.body_colour = self.body_var.get()
        r.stripe_colour = self.stripe_var.get()
        r.stripe_count = count
        self.dirty = True
        self._push_recipe()
        self._clear_result("Recipe changed - previous result cleared.")

    def _push_recipe(self):
        """Send the working recipe (copy) to the processing thread and refresh the info line."""
        r = self.recipe
        self.processor.send("recipe", r.copy() if r else None)
        self.processor.send("recipes", self.recipes.all())
        if r is None:
            return
        cal = "taught" if r.is_calibrated and r.body_ref.get("name") == r.body_colour \
            and r.stripe_ref.get("name") == r.stripe_colour else "presets only (not taught)"
        regions = "set" if r.has_regions else "NOT SET"
        self.recipe_info.config(text=(f"version {r.version}{' (unsaved changes)' if self.dirty else ''}\n"
                                      f"regions: {regions}   colours: {cal}\n"
                                      f"cable width: {f'{r.cable_width_px:.1f}' if r.cable_width_px else '-'} px   "
                                      f"stripe pitch: {r.geometry.get('expected_spacing_px') or '-'} px"))

    def save_recipe(self):
        if self.recipe is None:
            return
        stored = self.recipes.save(self.recipe)
        self.recipe = stored
        self.dirty = False
        self._load_recipe_list(stored.name)
        self.live_status.config(text=f"Saved recipe {stored.name} v{stored.version}")

    def new_recipe(self):
        name = simpledialog.askstring("New recipe", "Recipe name (cable type):", parent=self.root)
        if not name:
            return
        name = name.strip()
        if name in self.recipes.names():
            messagebox.showerror("New recipe", "A recipe with this name exists.")
            return
        base = self.recipe.copy() if self.recipe else Recipe(name)
        base.name = name
        base.version = 0
        base.body_ref = base.stripe_ref = None   # colours must be taught for the new cable
        base.notes = ""
        self.recipes.save(base)
        self.dirty = False
        self._load_recipe_list(name)

    def delete_recipe(self):
        if self.recipe and messagebox.askyesno("Delete recipe", f"Delete recipe '{self.recipe.name}'?"):
            self.recipes.delete(self.recipe.name)
            self.recipe = None
            self.dirty = False
            self._load_recipe_list()

    def _recipe_updated(self, recipe):
        self.recipe = recipe
        self.dirty = True
        self._push_recipe()
        self._clear_result("Recipe settings changed - previous result cleared.")

    # ------------------------------------------------------------------ regions
    def draw_region(self, which: str):
        if self.live_frame is None:
            messagebox.showinfo("Draw region", "No image yet - start the camera or open an image.")
            return
        label = "cable ROI (around the cable, with margin)" if which == "roi" else \
            "stripe inspection region (smaller, containing all stripes)"
        self.preview_hint.config(text=f"Drag a rectangle for the {label}.  Esc cancels.")
        colour = "#ffc800" if which == "roi" else "#ff00ff"
        self.root.bind("<Escape>", lambda e: self._end_draw())
        self.preview.start_selection(lambda rect: self._region_drawn(which, rect), colour)

    def _end_draw(self):
        self.preview.cancel_selection()
        self.root.unbind("<Escape>")
        self.preview_hint.config(text="")

    def _region_drawn(self, which: str, rect):
        self._end_draw()
        h, w = self.live_frame.shape[:2]
        r = self.recipe
        if r.image_size and tuple(r.image_size) != (w, h) and which == "inspection_region" and r.roi:
            messagebox.showwarning("Draw region", "Image resolution changed - redraw the ROI as well.")
        setattr(r, which, rect)
        r.image_size = (w, h)
        if r.roi and r.inspection_region and not rect_contains(r.roi, r.inspection_region):
            messagebox.showwarning("Draw region", "The stripe region should lie inside the cable ROI.")
        if which == "roi":
            r.cable_width_px = None  # re-learn width for the new view
        self.dirty = True
        self._push_recipe()
        self._clear_result("Region changed - previous result cleared.")

    # ------------------------------------------------------------------ camera
    def _start_source(self, source, label: str):
        if self.camera is not None:
            self.processor.send("source_change")
            self._clear_result(f"Source changed to {label}.")
        self.stop_camera()
        self.camera = CameraWorker(source, self.store, self.cam_events)
        self.camera.start()
        self.btn_cam.config(text="Stop Camera")
        self.cam_status.config(text=f"Camera: starting {label}...")
        self.preview_hint.config(text="")

    def toggle_camera(self):
        if self.camera is not None:
            self.stop_camera()
            return
        try:
            idx = int(self.cam_index.get())
        except ValueError:
            idx = 0
        settings = dict(self.recipe.camera) if self.recipe else {}
        settings["index"] = idx
        self._start_source(make_source(idx, settings), f"USB camera {idx}")

    def stop_camera(self):
        if self.camera is not None:
            self.camera.stop()
            self.camera.join(timeout=2.0)
            self.camera = None
        self.btn_cam.config(text="Start Camera")

    def open_file(self):
        paths = filedialog.askopenfilenames(
            title="Open image(s) or video", initialdir=str(SAMPLES_DIR if SAMPLES_DIR.exists() else Path.cwd()),
            filetypes=[("Images/videos", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.mp4 *.avi *.mov *.mkv"),
                       ("All files", "*.*")])
        if paths:
            self.open_files(list(paths))

    def open_files(self, paths: list[str]):
        """Open one or more images/videos; step through them with the arrow buttons/keys."""
        self.files = [str(p) for p in paths]
        self.file_idx = 0
        self._show_file()

    def step_file(self, delta: int):
        if len(self.files) > 1 and not self.preview.selecting:
            self.file_idx = (self.file_idx + delta) % len(self.files)
            self._show_file()

    def _show_file(self):
        if not self.files:
            return
        path = self.files[self.file_idx]
        self.file_lbl.config(text=f"{self.file_idx + 1}/{len(self.files)} {Path(path).stem[:12]}")
        self._start_source(make_source(path), Path(path).name)

    def load_demo(self, name: str):
        scene = synthetic.make(**DEMO_SCENES[name])
        self._start_source(ImageSource(scene.image, name=f"demo: {name}"), name)
        r = self.recipe
        if r is not None and not r.has_regions:
            # demo scenes share the nominal fixture geometry
            r.roi, r.inspection_region = scene.roi, scene.inspection_region
            r.image_size = (scene.image.shape[1], scene.image.shape[0])
            self.dirty = True
            self._push_recipe()

    def _current_frame(self):
        f = self.store.latest()
        return None if f is None else f.image.copy()

    # ------------------------------------------------------------------ dialogs
    def teach(self):
        if self.recipe is None:
            return
        if self._current_frame() is None:
            messagebox.showinfo("Teach", "Start the camera (or open an image) with a GOOD cable in place.")
            return
        TeachDialog(self.root, self.recipe, self._current_frame, self._recipe_updated)

    def settings(self):
        if self.recipe is not None:
            SettingsDialog(self.root, self.recipe, self._recipe_updated)

    def edit_presets(self):
        PresetDialog(self.root, self.presets, self._current_frame,
                     self.recipe.roi if self.recipe else None, self._presets_changed)

    def _presets_changed(self):
        self.processor.send("presets", self.presets)
        colours = self.presets.names()
        self.body_cb["values"] = colours
        self.stripe_cb["values"] = colours
        self._push_recipe()

    # ------------------------------------------------------------------ inspection
    def inspect(self):
        if self.recipe is None or not self.recipe.has_regions:
            messagebox.showinfo("Inspect", "Draw the cable ROI and stripe region first.")
            return
        self._clear_result("Inspecting - keep the cable still...", status="INSPECTING")
        self.processor.send("trigger")

    def toggle_auto(self):
        self.auto_on = not self.auto_on
        if self.auto_on and (self.recipe is None or not self.recipe.has_regions):
            self.auto_on = False
            messagebox.showinfo("Automatic inspection", "Draw the cable ROI and stripe region first.")
            return
        self.processor.send("auto", self.auto_on)
        self.btn_auto.config(text="Stop Automatic Inspection" if self.auto_on else "Start Automatic Inspection")

    def acknowledge(self):
        self.alert_active = False
        self.sounder.acknowledge()
        self.btn_ack.config(state="disabled")
        if self.latched is not None:
            self._show_status(self.latched.status)

    def _sound_changed(self):
        self.sounder.enabled = self.sound_var.get()
        if not self.sounder.enabled:
            self.sounder.acknowledge()

    def export(self):
        if self.latched is None:
            messagebox.showinfo("Export", "No inspection result to export yet.")
            return
        folder = filedialog.askdirectory(title="Export result to folder", initialdir=str(EXPORT_DIR.parent))
        if not folder:
            return
        files = export_result(self.latched, folder, self.recipe)
        messagebox.showinfo("Export", "Saved:\n" + "\n".join(p.name for p in files))

    # ------------------------------------------------------------------ results
    def _show_status(self, status: str, reason: str | None = None):
        bg, fg = STATUS_STYLE.get(status, ("#3c4043", "white"))
        self.result_lbl.config(text=status, bg=bg, fg=fg)
        if reason is not None:
            self.reason_lbl.config(text=reason)

    def _clear_result(self, reason: str, status: str = "READY"):
        self.latched = None
        if self.alert_active:
            self.acknowledge()
        self._show_status(status, reason)
        self.meas_txt.delete("1.0", "end")

    def _on_result(self, res):
        self.latched = res
        v = res.verdict
        text = "\n".join(v.reasons[:4]) if v.reasons else ""
        if v.identified:
            extra = f"  ({', '.join(v.identify_candidates)})" if len(v.identify_candidates) > 1 else ""
            text = f"Identified type: {v.identified}{extra}\n{text}"
        self._show_status(v.status, text)
        self._write_measurements(res)
        if res.frame is not None:
            mag = magnified_view(res.frame.measurement)
            if mag is not None:
                self.mag.show(mag)
        if v.status in (FAIL, CAMERA_ERROR):
            self.alert_active = True
            self.btn_ack.config(state="normal")
            if self.sound_var.get():
                self.sounder.alarm()
            else:
                self.root.bell()

    def _write_measurements(self, res):
        t = self.meas_txt
        t.delete("1.0", "end")
        v = res.verdict

        def ins(s, tag=None):
            t.insert("end", s, tag)

        ins(f"Cycle {res.cycle_id}  ({res.trigger}, {res.frames_used} frames)\n", "head")
        ins(f"Recipe {v.recipe_name} v{v.recipe_version}\n")
        e, o = v.expected, v.observed
        if e:
            ins(f"Expected: {e.get('body_colour')} body, {e.get('stripe_count')} x {e.get('stripe_colour')}\n")
        if o:
            ins(f"Observed: {o.get('body_colour', '-')} body {o.get('body_lab') or ''}\n")
            ins(f"          {o.get('stripe_count', 0)} stripes, angle {o.get('angle_deg', 0):.1f} deg, "
                f"width {o.get('cable_width_px', 0):.0f}px\n")
            for s in o.get("stripes", []):
                sp = f" spacing {s['spacing_px']:.0f}" if "spacing_px" in s else ""
                mm = f" ({s['width_mm']}mm)" if "width_mm" in s else ""
                ins(f"  #{s['index']} {s['colour']:<12} w {s['width_px']:.0f}px{mm} "
                    f"pos {s['position_px']:.0f}{sp}\n")
        if v.checks:
            ins("Checks:\n", "head")
            for name, ok, detail in v.checks:
                tag = "good" if ok else "bad" if ok is False else "warn"
                mark = "OK " if ok else "NOK" if ok is False else " ? "
                ins(f"  [{mark}] {name}: {detail}\n", tag)
        if res.frame_statuses and len(set(res.frame_statuses)) > 1:
            ins(f"Frame verdicts: {res.frame_statuses}\n", "warn")
        if res.frame is not None and res.frame.measurement.rejected:
            ins("Rejected candidates:\n", "head")
            for c in res.frame.measurement.rejected[:8]:
                ins(f"  x{c.start}-{c.end}: {c.reason}\n")

    # ------------------------------------------------------------------ polling (UI thread)
    def _poll(self):
        latest = None
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                kind = msg[0]
                if kind == "frame":
                    latest = msg
                elif kind == "result":
                    self._on_result(msg[1])
                elif kind == "phase":
                    self._on_phase(msg[1])
                elif kind == "setup_error":
                    self.live_status.config(text=f"Setup: {msg[1]}")
                elif kind == "camera_error":
                    self.cam_status.config(text=f"Camera: ERROR - {msg[1]}")
        except queue.Empty:
            pass
        try:
            while True:
                kind, text = self.cam_events.get_nowait()
                prefix = "ERROR - " if kind == "camera_error" else ""
                self.cam_status.config(text=f"Camera: {prefix}{text}")
        except queue.Empty:
            pass
        if latest is not None:
            self._render(latest[1], latest[2], latest[3])
        self.root.after(30, self._poll)

    def _on_phase(self, phase: str):
        self.phase_status.config(text=f"Cycle: {phase}{'  [AUTO]' if self.auto_on else ''}")
        if phase == ACQUIRING and self.result_lbl.cget("text") != "INSPECTING":
            self._clear_result("Inspecting - keep the cable still...", status="INSPECTING")
        elif phase == WAIT_REMOVAL and self.latched is not None:
            self.preview_hint.config(text="Remove the cable to arm the next automatic inspection.")
        elif phase == WAIT_CABLE:
            self.preview_hint.config(text="Automatic mode armed - place the next cable.")

    def _render(self, image, m, v):
        self.live_frame = image
        r = self.recipe
        roi = r.roi if r else None
        region = r.inspection_region if r else None
        if self.preview.selecting:
            self.preview.show(annotate(image, None, None, roi, region))
            return
        status = self.latched.status if self.latched is not None else None
        self.preview.show(annotate(image, m, status, roi, region))
        if m is not None:
            self.perf_status.config(text=f"processing {m.elapsed_ms:.0f} ms")
            live = f"live: {m.presence.lower()}"
            if m.presence != "ABSENT":
                live += f", {len(m.stripes)} stripe(s), angle {m.angle_deg:.1f} deg"
            if m.issues:
                live += f"  ({m.issues[0]})"
            self.live_status.config(text=live)
            if self.latched is None:
                mag = magnified_view(m)
                if mag is not None:
                    self.mag.show(mag)

    def _flash_tick(self):
        if self.alert_active and self.latched is not None:
            self._flash = not self._flash
            bg, fg = STATUS_STYLE.get(self.latched.status, ("#000000", "white"))
            self.result_lbl.config(bg=bg if self._flash else "#ffffff", fg=fg if self._flash else bg)
        self.root.after(450, self._flash_tick)

    def on_close(self):
        if self.dirty and self.recipe and messagebox.askyesno(
                "Unsaved changes", f"Save changes to recipe '{self.recipe.name}' before closing?"):
            self.recipes.save(self.recipe)
        self.sounder.acknowledge()
        self.processor.stop()
        self.stop_camera()
        self.root.destroy()


def main(argv: list[str] | None = None):
    import sys
    files = list(sys.argv[1:] if argv is None else argv)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - not Windows
        pass
    root = tk.Tk()
    app = App(root)
    if files:
        root.after(300, lambda: app.open_files(files))
    root.mainloop()
