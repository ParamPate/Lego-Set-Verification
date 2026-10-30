"""Guided launcher for the three existing LEGO verification applications.

The original programs remain the owners of their processing rules.  This file
only adds a small, single entry point with Back/Next navigation and transfers
the reviewed Brickognize CSV to the inventory application's import folder.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SEGMENT_SCRIPT = HERE / "manual_contour_tool" / "app.py"
SEGMENT_LAST_OUTPUT = HERE / "manual_contour_tool" / "last_output.txt"
RECOGNITION_SCRIPT = (
    PROJECT_ROOT
    / "part recognition"
    / "gallery_interface"
    / "gallery_gui.py"
)
RECOGNITION_ROOT = PROJECT_ROOT / "part recognition"
RECOGNITION_RESULTS = RECOGNITION_ROOT / "results" / "brickognize_results.csv"
RECOGNITION_PYTHON = RECOGNITION_ROOT / ".venv" / "Scripts" / "python.exe"
INVENTORY_ROOT = (
    PROJECT_ROOT
    / "Final_lego_verification_yushen"
    / "Final_lego_verification_yushen"
)
INVENTORY_SCRIPT = INVENTORY_ROOT / "tkinter_app.py"
INVENTORY_IMPORTS = INVENTORY_ROOT / "data" / "brickognize_results"


def segmented_folder_for(image_path: Path) -> Path:
    """Return the improved detector's default output folder."""
    return image_path.with_name(f"{image_path.stem}_separated_parts")


def count_unapproved_rows(source: Path = RECOGNITION_RESULTS) -> int:
    """Count recognition rows that have not been marked Ready."""
    if not source.is_file() or source.stat().st_size == 0:
        return 0
    with source.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if "Final Review Status" not in (reader.fieldnames or []):
            return 0
        return sum(
            1
            for row in reader
            if (row.get("Final Review Status") or "").strip().casefold() != "ready"
        )


def transfer_reviewed_csv(
    source: Path = RECOGNITION_RESULTS, include_unapproved: bool = False
) -> Path | None:
    """Copy results without overwriting imports, optionally counting warnings."""
    if not source.is_file() or source.stat().st_size == 0:
        return None
    INVENTORY_IMPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = INVENTORY_IMPORTS / f"brickognize_results_{stamp}.csv"
    counter = 2
    while destination.exists():
        destination = INVENTORY_IMPORTS / f"brickognize_results_{stamp}_{counter}.csv"
        counter += 1
    if not include_unapproved:
        shutil.copy2(source, destination)
        return destination

    # The inventory importer counts only rows marked Ready. Change that status
    # in the timestamped handoff copy, while preserving the recognition app's
    # original CSV and all predicted/corrected part and color values.
    with source.open("r", newline="", encoding="utf-8-sig") as source_handle:
        reader = csv.DictReader(source_handle)
        fieldnames = reader.fieldnames or []
        with destination.open("w", newline="", encoding="utf-8-sig") as destination_handle:
            writer = csv.DictWriter(destination_handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                if (row.get("Final Review Status") or "").strip().casefold() != "ready":
                    row["Final Review Status"] = "Ready"
                writer.writerow(row)
    return destination


class LegoWorkflowApp(tk.Tk):
    STEPS = (
        ("1", "Image analysis"),
        ("2", "Part and color review"),
        ("3", "Set inventory comparison"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.title("LEGO Set Verification Workflow")
        self.geometry("900x610")
        self.minsize(760, 540)
        self.current_step = 0
        self.process: subprocess.Popen[str] | None = None
        self.image_path = tk.StringVar()
        self.status = tk.StringVar(value="Open Step 1 and select all photographs for the queue.")
        self.output_folder: Path | None = None

        self._configure_style()
        self._build_shell()
        self.show_step(0)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("Heading.TLabel", font=("Segoe UI", 13, "bold"))
        style.configure("Step.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

    def _build_shell(self) -> None:
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="LEGO Set Verification", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="One guided workflow using the existing analysis, review, and inventory tools.",
        ).pack(anchor="w", pady=(2, 18))

        self.step_bar = ttk.Frame(outer)
        self.step_bar.pack(fill="x", pady=(0, 18))
        self.step_labels: list[ttk.Label] = []
        for column, (number, title) in enumerate(self.STEPS):
            label = ttk.Label(
                self.step_bar,
                text=f"{number}. {title}",
                style="Step.TLabel",
                anchor="center",
                padding=10,
            )
            label.grid(row=0, column=column, sticky="ew", padx=3)
            self.step_bar.columnconfigure(column, weight=1)
            self.step_labels.append(label)

        self.content = ttk.LabelFrame(outer, padding=22)
        self.content.pack(fill="both", expand=True)

        ttk.Label(outer, textvariable=self.status, wraplength=820).pack(
            anchor="w", fill="x", pady=(12, 8)
        )
        nav = ttk.Frame(outer)
        nav.pack(fill="x")
        self.back_button = ttk.Button(nav, text="Back", command=self.go_back)
        self.back_button.pack(side="left")
        self.next_button = ttk.Button(
            nav, text="Next", command=self.go_next, style="Primary.TButton"
        )
        self.next_button.pack(side="right")

    def show_step(self, index: int) -> None:
        self.current_step = index
        for child in self.content.winfo_children():
            child.destroy()
        for position, label in enumerate(self.step_labels):
            label.configure(relief="sunken" if position == index else "flat")
        self.back_button.configure(state="disabled" if index == 0 else "normal")
        self.next_button.configure(
            text="Finish" if index == len(self.STEPS) - 1 else "Next",
            state="normal",
        )
        (self._build_analysis, self._build_review, self._build_inventory)[index]()

    def _build_analysis(self) -> None:
        self.content.configure(text="Step 1 — Image analysis")
        ttk.Label(
            self.content,
            text=(
                "Separate photographs containing several LEGO pieces into individual images. "
                "Select all source photographs inside the analysis window and work down its queue."
            ),
            wraplength=760,
        ).pack(anchor="w", pady=(0, 18))

        ttk.Button(
            self.content,
            text="Start Step 1 image analysis",
            command=self.run_analysis,
            style="Primary.TButton",
        ).pack(anchor="w", pady=(0, 10))
        ttk.Label(
            self.content,
            text=(
                "The improved detector combines colour, brightness, and edges to handle shadows and "
                "uneven lighting. Keep pieces separated and fully inside the photograph for best results."
            ),
            wraplength=760,
        ).pack(anchor="w")

    def _build_review(self) -> None:
        self.content.configure(text="Step 2 — Individual images and color decisions")
        folder_text = str(self.output_folder) if self.output_folder else "the folder created in Step 1"
        ttk.Label(
            self.content,
            text=(
                "Open the existing SK BrickRescue review app. In that app choose Upload Images, "
                f"select the image*.png files from {folder_text}, then add any other images* folders "
                "and process and review the combined batch."
            ),
            wraplength=760,
        ).pack(anchor="w", pady=(0, 18))
        ttk.Button(
            self.content,
            text="Open part and color review",
            command=self.launch_recognition,
            style="Primary.TButton",
        ).pack(anchor="w")
        ttk.Label(
            self.content,
            text="Close the review window when finished. Its normal CSV and review files remain unchanged.",
            wraplength=760,
        ).pack(anchor="w", pady=(12, 0))

    def _build_inventory(self) -> None:
        self.content.configure(text="Step 3 — Set inventory comparison")
        ttk.Label(
            self.content,
            text=(
                "The reviewed Brickognize CSV will be copied into the inventory app's import folder, "
                "then the existing LEGO Set Verification app will open. Load the official set and import "
                "that CSV using the app's normal workflow."
            ),
            wraplength=760,
        ).pack(anchor="w", pady=(0, 18))
        ttk.Button(
            self.content,
            text="Prepare CSV and open inventory comparison",
            command=self.launch_inventory,
            style="Primary.TButton",
        ).pack(anchor="w")

    def choose_image(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Choose a LEGO photograph",
            filetypes=(("Image files", "*.png *.jpg *.jpeg"), ("All files", "*.*")),
        )
        if selected:
            self.image_path.set(selected)
            self.status.set("Ready to analyze the selected photograph.")

    def run_analysis(self) -> None:
        if self._process_running():
            messagebox.showinfo("Program running", "Close the currently running step first.", parent=self)
            return
        if SEGMENT_LAST_OUTPUT.exists():
            SEGMENT_LAST_OUTPUT.unlink()
        command = [sys.executable, str(SEGMENT_SCRIPT)]
        self.output_folder = None
        self._start_process(command, SEGMENT_SCRIPT.parent, "Image analysis")

    def launch_recognition(self) -> None:
        python = RECOGNITION_PYTHON if RECOGNITION_PYTHON.is_file() else Path(sys.executable)
        self._start_process([str(python), str(RECOGNITION_SCRIPT)], RECOGNITION_SCRIPT.parent, "Part/color review")

    def launch_inventory(self) -> None:
        unapproved_count = count_unapproved_rows()
        include_unapproved = False
        if unapproved_count:
            include_unapproved = messagebox.askyesno(
                "Count unapproved entries?",
                (
                    f"The recognition results contain {unapproved_count} entr"
                    f"{'y' if unapproved_count == 1 else 'ies'} with review warnings.\n\n"
                    "Should their predicted part and color values be counted in the "
                    "inventory comparison?\n\n"
                    "Choose No to count only entries marked Ready."
                ),
                parent=self,
            )
        copied = transfer_reviewed_csv(include_unapproved=include_unapproved)
        if copied is None:
            proceed = messagebox.askyesno(
                "No reviewed CSV found",
                "No non-empty part-recognition CSV was found. Open inventory comparison anyway?",
                parent=self,
            )
            if not proceed:
                return
            detail = "No CSV was copied."
        else:
            inclusion = (
                f" Included {unapproved_count} unapproved entries."
                if include_unapproved
                else " Unapproved entries were excluded."
                if unapproved_count
                else ""
            )
            detail = f"Copied reviewed results to {copied}.{inclusion}"
        self._start_process([sys.executable, str(INVENTORY_SCRIPT)], INVENTORY_ROOT, "Inventory comparison", detail)

    def _start_process(
        self, command: list[str], working_directory: Path, label: str, detail: str = ""
    ) -> None:
        if self._process_running():
            messagebox.showinfo("Program running", "Close the currently running step first.", parent=self)
            return
        try:
            self.process = subprocess.Popen(command, cwd=working_directory, text=True)
        except OSError as error:
            messagebox.showerror(f"Could not start {label}", str(error), parent=self)
            return
        self.status.set(f"{label} is running. {detail}".strip())
        self.after(500, lambda: self._poll_process(label))

    def _poll_process(self, label: str) -> None:
        if self.process is None:
            return
        code = self.process.poll()
        if code is None:
            self.after(500, lambda: self._poll_process(label))
            return
        self.process = None
        if code == 0:
            if label == "Image analysis" and SEGMENT_LAST_OUTPUT.is_file():
                saved_folder = SEGMENT_LAST_OUTPUT.read_text(encoding="utf-8").strip()
                if saved_folder:
                    self.output_folder = Path(saved_folder)
            suffix = (
                f" Output: {self.output_folder}"
                if label == "Image analysis" and self.output_folder
                else ""
            )
            self.status.set(f"{label} finished successfully.{suffix}")
        else:
            self.status.set(f"{label} stopped with exit code {code}. Check the console for details.")

    def _process_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def go_back(self) -> None:
        if self.current_step > 0:
            self.show_step(self.current_step - 1)

    def go_next(self) -> None:
        if self.current_step == len(self.STEPS) - 1:
            self.destroy()
        else:
            self.show_step(self.current_step + 1)


def main() -> None:
    app = LegoWorkflowApp()
    app.mainloop()


if __name__ == "__main__":
    main()
