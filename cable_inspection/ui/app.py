"""Main window: Cable Colour and Stripe Inspection."""

from __future__ import annotations

import queue
import time
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
        root.geometry("1500x920")
        root.minsize(1100, 700)
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
        self.alert_active = False
        self._flash = False
        self.sounder = AlertSounder(fallback=None)

        self._build_styles()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
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
        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6, pady=4)

        left = ttk.Frame(body)
        self.preview = ImageCanvas(left)
        self.preview.pack(fill="both", expand=True)
        self.preview_hint = ttk.Label(left, text="Start the camera, open an image/video or load a demo scene.")
        self.preview_hint.pack(anchor="w")
        body.add(left, weight=3)

        right = ttk.Frame(body, width=440)
        body.add(right, weight=1)

        # result indicator
        self.result_lbl = tk.Label(right, text="READY", font=("Segoe UI", 34, "bold"),
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
        self.mag = ImageCanvas(mf, height=200)
        self.mag.pack(fill="x")

        # measurements
        ms = ttk.LabelFrame(right, text="Measurements", padding=4)
        ms.pack(fill="both", expand=True, pady=6)
        self.meas_txt = tk.Text(ms, height=14, wrap="word", font=("Consolas", 9), relief="flat")
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
                                      f"cable width: {r.cable_width_px or '-'} px"))

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
