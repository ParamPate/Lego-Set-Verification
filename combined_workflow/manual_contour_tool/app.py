"""Run the adjustable LEGO contour tool with: python app.py"""

from __future__ import annotations

from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

from detector import Detection, Settings, analyze, make_overview, save_original_crops


LAST_OUTPUT_FILE = Path(__file__).with_name("last_output.txt")
DEFAULT_INPUT_FOLDER = Path(__file__).resolve().parents[2] / "image analysis"


class ContourTool(tk.Tk):
    FIELDS = (
        ("Saturation threshold", "saturation", 108),
        ("Brightness contrast", "brightness_contrast", 24),
        ("Very-light threshold", "very_light", 218),
        ("Background smoothing", "background_blur", 52),
        ("Opening kernel", "open_size", 3),
        ("Closing kernel", "close_size", 7),
        ("Closing passes", "close_iterations", 1),
        ("Minimum contour area", "minimum_area", 500),
        ("Crop padding", "padding", 35),
        ("White-bg saturation", "white_saturation", 70),
        ("White-bg contrast", "white_contrast", 24),
    )

    def __init__(self, initial_image: str | None = None) -> None:
        super().__init__()
        self.title("Adjustable LEGO Contour Tool")
        self.geometry("1180x780")
        self.minsize(900, 650)
        self.image_path = tk.StringVar()
        self.output_parent = tk.StringVar()
        self.status = tk.StringVar(value="Choose a photograph.")
        self.queue_position = tk.StringVar(value="No photos loaded")
        self.image_queue: list[Path] = []
        self.current_queue_index = -1
        self.completed_images: set[Path] = set()
        self.reject_border = tk.BooleanVar(value=True)
        self.background_mode = tk.StringVar(value="Auto")
        self.edit_mode = tk.StringVar(value="select")
        self.values = {name: tk.StringVar(value=str(default)) for _, name, default in self.FIELDS}
        self.source = self.mask = self.detections = None
        self.selected: set[int] = set()
        self.preview_scale = 1.0
        self.preview_photo = None
        self.drag_start: tuple[int, int] | None = None
        self._build()
        if initial_image:
            path = Path(initial_image)
            if path.is_file():
                self.load_image_queue([path])

    def _build(self) -> None:
        top = ttk.Frame(self, padding=14)
        top.pack(fill="x")
        ttk.Label(top, text="Photograph:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.image_path).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="Select photos…", command=self.choose_image).grid(row=0, column=2)

        queue_row = ttk.Frame(top)
        queue_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(queue_row, textvariable=self.queue_position, width=18).pack(side="left")
        self.queue_selector = ttk.Combobox(queue_row, state="readonly")
        self.queue_selector.pack(side="left", fill="x", expand=True, padx=8)
        self.queue_selector.bind("<<ComboboxSelected>>", self.choose_queued_image)
        self.previous_image_button = ttk.Button(
            queue_row, text="Previous", command=lambda: self.move_in_queue(-1), state="disabled"
        )
        self.previous_image_button.pack(side="left", padx=(0, 4))
        self.next_image_button = ttk.Button(
            queue_row, text="Next", command=lambda: self.move_in_queue(1), state="disabled"
        )
        self.next_image_button.pack(side="left")

        ttk.Label(top, text="Save under:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(top, textvariable=self.output_parent).grid(row=2, column=1, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(top, text="Browse…", command=self.choose_output).grid(row=2, column=2, pady=(8, 0))
        top.columnconfigure(1, weight=1)

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=14)
        controls = ttk.LabelFrame(body, text="Detection settings", padding=12)
        preview = ttk.LabelFrame(body, text="Numbered preview", padding=8)
        body.add(controls, weight=0)
        body.add(preview, weight=1)

        ttk.Label(controls, text="Background").grid(row=0, column=0, sticky="w", pady=3)
        background_selector = ttk.Combobox(
            controls,
            textvariable=self.background_mode,
            values=("Auto", "Dark", "White"),
            state="readonly",
            width=9,
        )
        background_selector.grid(row=0, column=1, padx=(8, 0), pady=3)
        for row, (label, name, _) in enumerate(self.FIELDS, start=1):
            ttk.Label(controls, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(controls, textvariable=self.values[name], width=10).grid(row=row, column=1, padx=(8, 0))
        row = len(self.FIELDS) + 1
        ttk.Checkbutton(controls, text="Reject small border artifacts", variable=self.reject_border).grid(row=row, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Button(controls, text="Analyze / Refresh", command=self.run_analysis).grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=4)
        mode_frame = ttk.LabelFrame(controls, text="Box editing", padding=6)
        mode_frame.grid(row=row + 2, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Radiobutton(mode_frame, text="Select / delete", value="select", variable=self.edit_mode).pack(anchor="w")
        ttk.Radiobutton(mode_frame, text="Draw new box", value="add", variable=self.edit_mode).pack(anchor="w")
        ttk.Radiobutton(mode_frame, text="Resize selected box", value="resize", variable=self.edit_mode).pack(anchor="w")
        ttk.Button(controls, text="Remove selected boxes", command=self.remove_selected).grid(row=row + 3, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(controls, text="Save original crops", command=self.save_crops).grid(row=row + 4, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(
            controls,
            text="Save crops & next",
            command=lambda: self.save_crops(advance=True),
        ).grid(row=row + 5, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(controls, text="Select false boxes in red to remove them. To resize, select exactly one box, choose Resize, then drag its replacement. Draw adds a new box. Crop padding is added when saving.", wraplength=240).grid(row=row + 6, column=0, columnspan=2, sticky="w", pady=(12, 0))

        self.preview_canvas = tk.Canvas(preview, background="#202020", highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Button-1>", self.begin_box_action)
        self.preview_canvas.bind("<B1-Motion>", self.drag_box_action)
        self.preview_canvas.bind("<ButtonRelease-1>", self.finish_box_action)
        ttk.Label(self, textvariable=self.status, padding=14).pack(fill="x")

    def choose_image(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select one or more photographs",
            initialdir=DEFAULT_INPUT_FOLDER,
            filetypes=(("Images", "*.jpg *.jpeg *.png"), ("All files", "*.*")),
        )
        if paths:
            self.load_image_queue([Path(path) for path in paths])

    def _queue_labels(self) -> list[str]:
        """Return unambiguous queue labels and mark already saved photos."""
        return [
            f"{'✓ ' if path in self.completed_images else ''}{index + 1}. {path.name}"
            for index, path in enumerate(self.image_queue)
        ]

    def load_image_queue(self, paths: list[Path]) -> None:
        """Replace the working queue and analyze its first photograph."""
        self.image_queue = [path.resolve() for path in paths if path.is_file()]
        self.completed_images.clear()
        if not self.image_queue:
            return
        if not self.output_parent.get():
            self.output_parent.set(str(self.image_queue[0].parent))
        self.queue_selector.configure(values=self._queue_labels())
        self.load_queue_index(0, analyze_now=True)

    def load_queue_index(self, index: int, analyze_now: bool = True) -> None:
        """Display one queued photograph and optionally run contour analysis."""
        if not 0 <= index < len(self.image_queue):
            return
        self.current_queue_index = index
        path = self.image_queue[index]
        self.image_path.set(str(path))
        self.queue_selector.current(index)
        self.queue_position.set(f"Photo {index + 1} of {len(self.image_queue)}")
        self.previous_image_button.configure(state="normal" if index > 0 else "disabled")
        self.next_image_button.configure(
            state="normal" if index + 1 < len(self.image_queue) else "disabled"
        )
        self.source = self.mask = self.detections = None
        self.selected.clear()
        self.preview_canvas.delete("all")
        self.status.set(f"Loaded {path.name}.")
        if analyze_now:
            self.after_idle(self.run_analysis)

    def choose_queued_image(self, _event=None) -> None:
        self.load_queue_index(self.queue_selector.current(), analyze_now=True)

    def move_in_queue(self, offset: int) -> None:
        self.load_queue_index(self.current_queue_index + offset, analyze_now=True)

    def choose_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_parent.set(path)

    def settings(self) -> Settings:
        numbers = {name: int(self.values[name].get()) for _, name, _ in self.FIELDS}
        return Settings(
            **numbers,
            reject_border_artifacts=self.reject_border.get(),
            background_mode=self.background_mode.get(),
        )

    def run_analysis(self) -> None:
        try:
            path = Path(self.image_path.get())
            if not path.is_file():
                raise ValueError("Choose an existing photograph first.")
            settings = self.settings()
            self.source, self.mask, self.detections = analyze(path, settings)
            self.selected.clear()
            self.refresh_preview()
            self.status.set(f"Detected {len(self.detections)} contours. Adjust values and refresh, or save the original crops.")
        except (ValueError, OSError) as error:
            messagebox.showerror("Could not analyze image", str(error), parent=self)

    def refresh_preview(self) -> None:
        if self.source is None:
            return
        overview = make_overview(
            self.source, self.detections, self.settings().padding, self.selected
        )
        rgb = cv2.cvtColor(overview, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        original_width = image.width
        image.thumbnail((820, 610), Image.Resampling.LANCZOS)
        self.preview_scale = image.width / original_width
        self.preview_photo = ImageTk.PhotoImage(image)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(0, 0, image=self.preview_photo, anchor="nw")
        self.preview_canvas.configure(scrollregion=(0, 0, image.width, image.height))

    def _source_point(self, event) -> tuple[int, int]:
        """Convert a preview-canvas event into clamped source coordinates."""
        height, width = self.source.shape[:2]
        x = max(0, min(width - 1, round(event.x / self.preview_scale)))
        y = max(0, min(height - 1, round(event.y / self.preview_scale)))
        return x, y

    def begin_box_action(self, event) -> None:
        if self.source is None or self.preview_scale <= 0:
            return
        if self.edit_mode.get() == "select":
            self.toggle_box(event)
            return
        if self.edit_mode.get() == "resize" and len(self.selected) != 1:
            self.status.set("Select exactly one red box before resizing it.")
            return
        self.drag_start = self._source_point(event)

    def drag_box_action(self, event) -> None:
        if self.drag_start is None:
            return
        x1, y1 = self.drag_start
        x2, y2 = self._source_point(event)
        self.preview_canvas.delete("manual_drag")
        self.preview_canvas.create_rectangle(
            x1 * self.preview_scale,
            y1 * self.preview_scale,
            x2 * self.preview_scale,
            y2 * self.preview_scale,
            outline="#00BFFF",
            width=3,
            dash=(7, 4),
            tags="manual_drag",
        )

    def finish_box_action(self, event) -> None:
        if self.drag_start is None:
            return
        start_x, start_y = self.drag_start
        end_x, end_y = self._source_point(event)
        self.drag_start = None
        self.preview_canvas.delete("manual_drag")
        x1, x2 = sorted((start_x, end_x))
        y1, y2 = sorted((start_y, end_y))
        if x2 - x1 < 8 or y2 - y1 < 8:
            self.status.set("Drag a larger rectangle to create a box.")
            return
        contour = np.array(
            [[[x1, y1]], [[x2, y1]], [[x2, y2]], [[x1, y2]]],
            dtype=np.int32,
        )
        detection = Detection(contour, x1, y1, x2 - x1, y2 - y1, float((x2 - x1) * (y2 - y1)))
        if self.edit_mode.get() == "resize":
            index = next(iter(self.selected))
            self.detections[index] = detection
            self.selected = {index}
            message = f"Resized box {index + 1}."
        else:
            self.detections.append(detection)
            self.selected = {len(self.detections) - 1}
            message = f"Added box {len(self.detections)}."
        self.refresh_preview()
        self.status.set(message + " The red box is selected.")

    def toggle_box(self, event) -> None:
        if not self.detections or self.preview_scale <= 0:
            return
        x = event.x / self.preview_scale
        y = event.y / self.preview_scale
        padding = self.settings().padding
        matches = []
        for index, detection in enumerate(self.detections):
            if (
                detection.x - padding <= x <= detection.x + detection.width + padding
                and detection.y - padding <= y <= detection.y + detection.height + padding
            ):
                matches.append((detection.width * detection.height, index))
        if not matches:
            return
        index = min(matches)[1]
        if index in self.selected:
            self.selected.remove(index)
        else:
            self.selected.add(index)
        self.refresh_preview()
        self.status.set(f"{len(self.selected)} box(es) selected in red.")

    def remove_selected(self) -> None:
        if not self.selected:
            self.status.set("Click one or more false boxes in the preview first.")
            return
        removed = len(self.selected)
        self.detections = [
            detection
            for index, detection in enumerate(self.detections)
            if index not in self.selected
        ]
        self.selected.clear()
        self.refresh_preview()
        self.status.set(f"Removed {removed} false box(es); {len(self.detections)} remain.")

    def save_crops(self, advance: bool = False) -> None:
        if self.source is None or self.detections is None:
            self.run_analysis()
            if self.source is None:
                return
        try:
            parent = Path(self.output_parent.get())
            parent.mkdir(parents=True, exist_ok=True)
            folder, analysis_folder = save_original_crops(self.source, self.mask, self.detections, parent, self.settings().padding)
            LAST_OUTPUT_FILE.write_text(str(folder.resolve()), encoding="utf-8")
            if 0 <= self.current_queue_index < len(self.image_queue):
                self.completed_images.add(self.image_queue[self.current_queue_index])
                self.queue_selector.configure(values=self._queue_labels())
                self.queue_selector.current(self.current_queue_index)
            self.status.set(f"Saved {len(self.detections)} untouched crops to {folder}")
            messagebox.showinfo("Crops saved", f"Brickognize images only:\n{folder}\n\nDiagnostics:\n{analysis_folder}", parent=self)
            if advance and self.current_queue_index + 1 < len(self.image_queue):
                self.load_queue_index(self.current_queue_index + 1, analyze_now=True)
            elif advance:
                self.status.set("All queued photographs have been analyzed and saved.")
        except (ValueError, OSError) as error:
            messagebox.showerror("Could not save crops", str(error), parent=self)


if __name__ == "__main__":
    ContourTool(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
