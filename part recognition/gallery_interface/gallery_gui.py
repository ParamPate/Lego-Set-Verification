"""
Provide the SK BrickRescue employee-facing desktop application.

This file owns presentation and user interaction only. It displays images,
starts batch work in background threads, shows saved results, and guides an
employee through color or part review. Recognition and file-writing rules
remain in the backend modules imported below.

The interface follows this high-level flow:
    1. Select one or more LEGO photographs.
    2. Restore any results that were already saved.
    3. Send only new photographs to the backend.
    4. Display the uploaded and BrickLink images side by side.
    5. Allow color verification for every result and require review when
       confidence is low.

Tkinter widgets must be changed only from the main GUI thread. Worker threads
therefore place messages into queues, and scheduled queue-checking functions
apply those updates safely.
"""

# ---------------------------------------------------------------------------
# MAINTAINER MAP: DESKTOP APPLICATION
# ---------------------------------------------------------------------------
# This is the employee-facing orchestration layer. It does not duplicate the
# Brickognize parsing, CSV writing, review persistence, or Excel-building rules
# owned by the imported backend modules.
#
# Primary employee workflow:
# 1. upload_images() builds the visible queue without modifying source photos.
# 2. start_batch_processing() validates state and launches a worker thread.
# 3. run_batch_processing() identifies only new filenames and places messages
#    on processing_queue.
# 4. check_processing_queue() applies results on Tkinter's main thread.
# 5. show_selected_image() reloads previews and saved reviews.
# 6. confirm_selected_color() or the rescan workflow saves optional decisions.
# 7. export_excel_report() creates a snapshot after processing is idle.
# 8. start_new_batch() clears only the screen or archives a completed LEGO set.
#
# Threading rule:
# - Worker threads may perform web requests, recognition, and parsing.
# - They must not directly configure Tkinter widgets.
# - Queues plus window.after(...) move results back to the GUI thread.
#
# Saving rule:
# - Every successful save updates a nearby message/control and the shared
#   Last saved indicator so the employee receives visible confirmation.
#
# Batch rule:
# - Clear Screen Only preserves active CSV/JSON files.
# - Finish & Start New LEGO Set archives active outputs into a unique timestamped
#   folder before resetting the workspace.
#
# Image rule:
# - Previews preserve aspect ratio. Never crop employee photos here; image
#   preparation belongs to a separate workflow.
#
# Maintenance rule:
# - When changing result dictionary keys, also inspect main.py,
#   review_manager.py, excel_report.py, filters, saved-result restoration,
#   comparison displays, tooltips, in-app help, and README.md.
# ---------------------------------------------------------------------------

import csv
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

# This interface lives beside the classic ``src`` folder but intentionally
# reuses the same recognition and persistence backends.
CLASSIC_SRC = Path(__file__).resolve().parent.parent / "src"
if str(CLASSIC_SRC) not in sys.path:
    sys.path.insert(0, str(CLASSIC_SRC))

from PIL import Image, ImageDraw, ImageTk
from main import (
    identify_part,
    append_result_to_csv,
)
from review_policy import apply_review_policy, has_identifiers, needs_attention, review_group
from excel_report import create_excel_report
from bricklink_colors import BRICKLINK_REVIEW_COLORS
from part_color_service import get_part_color_options
from review_manager import (
    approve_reviewed_result,
    clear_color_review,
    load_color_review,
    load_part_review,
    save_color_review,
    save_part_review,
    sync_reviewed_result_to_csv,
)

from io import BytesIO
import requests

# ---------------------------------------------------------------------------
# Project files and visual theme
# ---------------------------------------------------------------------------

# Project locations used by the interface.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE_FOLDER = PROJECT_ROOT.parent / "image analysis"
LOGO_FILE = PROJECT_ROOT / "assets" / "sk_brickrescue_logo.png"
RESULTS_FOLDER = PROJECT_ROOT / "results"
API_RESPONSES_FILE = RESULTS_FOLDER / "brickognize_responses.json"
CAPTURED_IMAGES_FOLDER = RESULTS_FOLDER / "captured_images"
BATCH_ARCHIVE_FOLDER = RESULTS_FOLDER / "completed_batches"

# SK BrickRescue application palette.
APP_BACKGROUND = "#F3F5F7"
CARD_BACKGROUND = "#FFFFFF"

PRIMARY_GREEN = "#038B00"
PRIMARY_GREEN_HOVER = "#027300"

SECONDARY_BUTTON = "#002EC4"
SECONDARY_BUTTON_HOVER = "#00249A"

BRAND_BLUE = "#2CA6FF"
BRAND_YELLOW = "#FEE400"

TEXT_PRIMARY = "#1F2933"
TEXT_SECONDARY = "#66727D"

REVIEW_BADGE_BACKGROUND = "#FEF3C7"
REVIEW_BADGE_TEXT = "#92400E"

CLEAR_BADGE_BACKGROUND = "#DCFCE7"
CLEAR_BADGE_TEXT = "#166534"

NEUTRAL_BADGE_BACKGROUND = "#EEF2F6"
NEUTRAL_BADGE_TEXT = "#66727D"

LOW_CONFIDENCE_BACKGROUND = "#FEE2E2"
LOW_CONFIDENCE_TEXT = "#991B1B"


# CLASS GUIDE: ToolTip
# Purpose: Display a short delayed explanation beside a hovered GUI control.
class ToolTip:
    """Display a short delayed explanation beside a hovered GUI control."""

    # FUNCTION GUIDE: __init__
    # Purpose: Attach delayed explanatory text to one Tkinter widget.
    def __init__(self, widget, text, delay=450):
        """Attach delayed explanatory text to one Tkinter widget."""
        self.widget = widget
        self.text = text
        self.delay = delay
        self.after_id = None
        self.tip_window = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    # FUNCTION GUIDE: schedule
    # Purpose: Start the delay before displaying the tooltip.
    def schedule(self, _event=None):
        """Start the delay before displaying the tooltip."""
        self.cancel_schedule()
        self.after_id = self.widget.after(self.delay, self.show)

    # FUNCTION GUIDE: cancel_schedule
    # Purpose: Cancel a pending tooltip display.
    def cancel_schedule(self):
        """Cancel a pending tooltip display."""
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    # FUNCTION GUIDE: show
    # Purpose: Display the tooltip beside its associated widget.
    def show(self):
        """Display the tooltip beside its associated widget."""
        self.after_id = None

        if self.tip_window is not None or not self.text:
            return

        pointer_x = self.widget.winfo_pointerx() + 14
        pointer_y = self.widget.winfo_pointery() + 18
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.wm_overrideredirect(True)
        self.tip_window.wm_geometry(f"+{pointer_x}+{pointer_y}")

        tooltip_label = tk.Label(
            self.tip_window,
            text=self.text,
            justify="left",
            wraplength=330,
            background="#172033",
            foreground="#FFFFFF",
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=7,
            font=("Segoe UI", 9),
        )
        tooltip_label.pack()

    # FUNCTION GUIDE: hide
    # Purpose: Close the tooltip window when it is no longer needed.
    def hide(self, _event=None):
        """Close the tooltip window when it is no longer needed."""
        self.cancel_schedule()

        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


active_tooltips = []


# FUNCTION GUIDE: add_tooltip
# Purpose: Attach and retain one tooltip for the lifetime of the application.
def add_tooltip(widget, text):
    """Attach and retain one tooltip for the lifetime of the application."""

    active_tooltips.append(ToolTip(widget, text))


# FUNCTION GUIDE: fit_window_to_screen
# Purpose: Center a window at a useful size that never exceeds its monitor.
def fit_window_to_screen(
    target_window,
    requested_width,
    requested_height,
    minimum_width=500,
    minimum_height=420,
    horizontal_margin=100,
    vertical_margin=140,
):
    """Center a window at a useful size that never exceeds its monitor."""

    screen_width = target_window.winfo_screenwidth()
    screen_height = target_window.winfo_screenheight()
    available_width = max(screen_width - horizontal_margin, 420)
    available_height = max(screen_height - vertical_margin, 360)
    safe_minimum_width = min(minimum_width, available_width)
    safe_minimum_height = min(minimum_height, available_height)
    window_width = max(
        min(requested_width, available_width),
        safe_minimum_width,
    )
    window_height = max(
        min(requested_height, available_height),
        safe_minimum_height,
    )
    position_x = max((screen_width - window_width) // 2, 0)
    position_y = max((screen_height - window_height) // 2, 0)
    target_window.geometry(
        f"{window_width}x{window_height}+{position_x}+{position_y}"
    )
    target_window.minsize(
        safe_minimum_width,
        safe_minimum_height,
    )

    return window_width, window_height


# FUNCTION GUIDE: show_save_confirmation
# Purpose: Keep the latest successful save visible in the main toolbar.
def show_save_confirmation(message):
    """Keep the latest successful save visible in the main toolbar."""

    saved_time = datetime.now().strftime("%I:%M:%S %p").lstrip("0")
    save_activity_label.configure(
        text=f"✓ Last saved {saved_time}: {message}",
        text_color="#15803D",
    )


# FUNCTION GUIDE: set_excel_export_enabled
# Purpose: Enable the Excel shortcut or fade it into a non-clickable state.
def set_excel_export_enabled(enabled):
    """Enable the Excel shortcut or fade it into a non-clickable state."""

    # Visual and functional state move together. A green icon must be usable;
    # a faded icon must be disabled so it cannot export incomplete data.
    excel_export_button.configure(
        state="normal" if enabled else "disabled",
        image=(
            excel_export_icon
            if enabled
            else excel_export_icon_disabled
        ),
        fg_color="#107C41" if enabled else "#CBD5D1",
        hover_color="#0B6334" if enabled else "#CBD5D1",
    )


# FUNCTION GUIDE: update_excel_export_state
# Purpose: Enable Excel only for loaded results outside active processing.
def update_excel_export_state():
    """Enable Excel only for loaded results outside active processing."""

    has_loaded_results = bool(processed_results)
    processing_active = process_button.cget("state") == "disabled"
    set_excel_export_enabled(
        has_loaded_results and not processing_active
    )


# ---------------------------------------------------------------------------
# Active application state
# ---------------------------------------------------------------------------

# These collections describe only the batch currently visible in the GUI.
# Permanent results remain in the results folder and are managed separately.
# Selected order, filtered visibility, processed data, saved/restored origin,
# and worker progress are separate concepts; do not collapse these collections
# into one structure without updating every queue, filter, and summary path.

# Stores the full file path for every selected image.
selected_image_paths = []

# Stores the paths currently displayed in the filtered image list.
visible_image_paths = []

# Stores each processed result using its image path as the key.
processed_results = {}
failed_image_paths = set()

# Review decisions update the displayed result immediately, but their final
# CSV columns are written together when the operator chooses Update All
# Statuses. Image name is the persistent CSV key, so newer decisions replace
# older pending decisions for the same photograph.
pending_csv_reviews = {}


def queue_csv_review(result):
    """Keep one pending CSV update for a reviewed image."""
    image_name = result.get("Image", "")
    if image_name:
        pending_csv_reviews[image_name] = result
    update_all_statuses_button.configure(
        state="normal" if pending_csv_reviews else "disabled",
        text=f"Update All Statuses ({len(pending_csv_reviews)})",
    )
    save_activity_label.configure(
        text=f"{len(pending_csv_reviews)} approval status change(s) waiting to be saved.",
        text_color="#B45309",
    )


def update_all_approval_statuses():
    """Write every pending in-memory approval to the results CSV."""
    if not pending_csv_reviews:
        return True
    failed = []
    for image_name, result in list(pending_csv_reviews.items()):
        try:
            if not sync_reviewed_result_to_csv(result):
                failed.append(image_name)
        except OSError as error:
            print(f"CSV review update error for {image_name}: {error}")
            failed.append(image_name)
    for image_name in list(pending_csv_reviews):
        if image_name not in failed:
            pending_csv_reviews.pop(image_name, None)
    update_all_statuses_button.configure(
        state="normal" if pending_csv_reviews else "disabled",
        text=f"Update All Statuses ({len(pending_csv_reviews)})",
    )
    if failed:
        processing_status_label.configure(
            text=f"Could not save {len(failed)} approval status change(s).",
            foreground="#B91C1C",
        )
        return False
    show_save_confirmation("all approval statuses updated")
    processing_status_label.configure(
        text="All pending approval statuses were saved to the CSV.",
        foreground="#15803D",
    )
    return True

# Retain full-resolution copies of the two currently displayed images for
# click-to-enlarge viewing. Preview resizing never crops these sources.
full_preview_images = {
    "uploaded": None,
    "reference": None,
}

# Stores an alternate-angle image selected for a part review.
# The original image path is the key and the rescan image is the value.
pending_rescan_images = {}

# Carries rescan results safely back to the GUI.
rescan_queue = queue.Queue()

# Stores completed alternate-angle results by original image path.
completed_rescans = {}

# Tracks repeated Part IDs discovered during the current processing run.
duplicate_part_warnings = []

# Carries processing updates safely from the worker to the GUI.
processing_queue = queue.Queue()

# Carries BrickLink part-color lookups back to Tkinter's main thread.
color_option_queue = queue.Queue()

# Catalog image downloads must never block list selection or navigation.
reference_image_queue = queue.Queue()
reference_image_cache = {}
reference_image_loading = set()
reference_image_failures = set()

# Stores the limited, part-specific color choices for each image.
color_options_by_image = {}
# Tracks active network lookups without treating the temporary one-color
# placeholder as a completed result.
color_option_requests = set()

# Tracks which active rows were restored from CSV and which were actually
# processed during the current batch session. Keeping these separate prevents
# previously saved work from inflating the "Newly Processed" counter.
previously_saved_paths = set()
newly_processed_paths = set()

# Friendly list symbols and colors make each image's state recognizable
# without requiring employees to interpret bracketed status words.
IMAGE_ROW_STYLES = {
    "new": ("", "#F7F9FA", TEXT_PRIMARY),
    "recorded": ("↻", "#DBEAFE", "#1D4ED8"),
    "processing": ("…", "#E0F2FE", "#0369A1"),
    "done": ("✓", "#DCFCE7", "#166534"),
    "review": ("⚠", "#FEF3C7", "#92400E"),
    "failed": ("✕", "#FEE2E2", "#991B1B"),
}


# FUNCTION GUIDE: get_recorded_csv_images
# Purpose: Return image filenames that already have a saved CSV row.
def get_recorded_csv_images():
    """Return image filenames that already have a saved CSV row."""

    csv_file = RESULTS_FOLDER / "brickognize_results.csv"

    if not csv_file.exists() or csv_file.stat().st_size == 0:
        return set()

    try:
        with csv_file.open(
            "r",
            newline="",
            encoding="utf-8",
        ) as results_file:
            return {
                row.get("Image", "")
                for row in csv.DictReader(results_file)
                if row.get("Image")
            }

    except (OSError, csv.Error):
        return set()


# FUNCTION GUIDE: get_recorded_csv_results
# Purpose: Load saved CSV rows by filename for reuse in the active GUI batch.
def get_recorded_csv_results():
    """
    Load saved CSV rows by filename for reuse in the active GUI batch.

    Final employee-reviewed values take priority when present. Original
    prediction columns remain in the dictionary for traceability.
    """

    csv_file = RESULTS_FOLDER / "brickognize_results.csv"

    if not csv_file.exists() or csv_file.stat().st_size == 0:
        return {}

    try:
        with csv_file.open(
            "r",
            newline="",
            encoding="utf-8",
        ) as results_file:
            saved_results = {}

            for row in csv.DictReader(results_file):
                image_name = row.get("Image", "")

                if not image_name:
                    continue

                restored_result = dict(row)

                # Apply final review values to the display fields while
                # retaining every original CSV column in restored_result.
                final_field_pairs = [
                    ("Final Part ID", "Part ID"),
                    ("Final Part Name", "Part Name"),
                    ("Final Part Confidence", "Part Confidence"),
                    ("Final LEGO Color", "LEGO Color"),
                    ("Final BrickLink Color ID", "Confirmed BrickLink Color ID"),
                    ("Final BrickLink URL", "BrickLink URL"),
                ]

                for final_field, display_field in final_field_pairs:
                    final_value = row.get(final_field, "")
                    if final_value:
                        restored_result[display_field] = final_value

                if row.get("Part Review Completed") == "Yes":
                    restored_result["Part Review Required"] = "No"

                if row.get("Color Review Completed") == "Yes":
                    restored_result["Color Review Required"] = "No"

                saved_results[image_name] = apply_review_policy(restored_result)

            return saved_results

    except (OSError, csv.Error):
        return {}


# FUNCTION GUIDE: get_image_row_state
# Purpose: Return the most useful filename-list state for one image.
def get_image_row_state(image_path):
    """Return the most useful filename-list state for one image."""

    normalized_path = str(Path(image_path))
    result = processed_results.get(normalized_path)

    if result:
        if needs_attention(result):
            return "review"
        if (
            result.get("Part Review Completed") == "Yes"
            and result.get("Color Review Completed") == "Yes"
        ):
            return "done"
        if (
            result.get("Part Review Required") == "Yes"
            or result.get("Color Review Required") == "Yes"
        ):
            return "review"
        return "recorded"

    if normalized_path in failed_image_paths:
        return "failed"

    if Path(image_path).name in get_recorded_csv_images():
        return "recorded"

    return "new"


# FUNCTION GUIDE: set_image_list_row
# Purpose: Insert or replace one styled row in the Selected Images list.
def set_image_list_row(row_index, image_path, state=None):
    """Insert or replace one styled row in the Selected Images list."""

    if state is None:
        state = get_image_row_state(image_path)

    symbol, background, foreground = IMAGE_ROW_STYLES[state]
    image_name = Path(image_path).name
    list_text = f"{symbol} {image_name}".strip()

    if row_index < image_listbox.size():
        image_listbox.delete(row_index)

    image_listbox.insert(row_index, list_text)
    image_listbox.itemconfig(
        row_index,
        background=background,
        foreground=foreground,
    )


# FUNCTION GUIDE: request_part_color_options
# Purpose: Load the known BrickLink colors without freezing the interface.
def request_part_color_options(result):
    """
    Load the known BrickLink colors without freezing the interface.

    BrickLink is a network resource, so the lookup runs in a worker
    thread.  ``check_color_option_queue`` applies the result safely on
    Tkinter's main thread.
    """

    image_name = result.get("Image", "Unknown")
    part_id = result.get("Part ID", "Unknown")
    predicted_color = result.get("LEGO Color", "Unknown")
    request_key = (image_name, str(part_id))
    if request_key in color_option_requests:
        return
    color_option_requests.add(request_key)

    # FUNCTION GUIDE: load_options
    # Purpose: Perform the BrickLink lookup away from Tkinter's main thread.
    def load_options():
        """Perform the BrickLink lookup away from Tkinter's main thread."""

        options, source = get_part_color_options(
            part_id,
            predicted_color,
        )
        color_option_queue.put(
            (image_name, part_id, options, source)
        )

    # A daemon thread cannot prevent the desktop application from closing.
    # Its result returns through color_option_queue for a main-thread update.
    threading.Thread(
        target=load_options,
        daemon=True,
    ).start()


# FUNCTION GUIDE: check_color_option_queue
# Purpose: Apply finished part-color lookups to the active review control.
def check_color_option_queue():
    """Apply finished part-color lookups to the active review control."""

    try:
        while True:
            image_name, part_id, options, source = (
                color_option_queue.get_nowait()
            )
            color_option_requests.discard((image_name, str(part_id)))
            color_options_by_image[image_name] = options

            selected_indices = image_listbox.curselection()
            if not selected_indices:
                continue

            selected_path = Path(
                visible_image_paths[selected_indices[0]]
            )
            selected_result = processed_results.get(
                str(selected_path)
            )
            if not selected_result:
                continue

            is_current_result = (
                selected_result.get("Image") == image_name
                and str(selected_result.get("Part ID")) == str(part_id)
            )
            if not is_current_result:
                continue

            # Never replace a row's color with the first alphabetic option.
            # A delayed lookup can finish just after Previous/Next navigation;
            # the selected result's saved color must remain authoritative.
            displayed_color = selected_result.get("LEGO Color", "Unknown")
            displayed_color_id = (
                selected_result.get("Confirmed BrickLink Color ID")
                or selected_result.get("BrickLink Color ID")
                or BRICKLINK_REVIEW_COLORS.get(displayed_color)
            )
            merged_options = dict(options)
            if displayed_color_id:
                merged_options[displayed_color] = displayed_color_id
            color_options_by_image[image_name] = merged_options

            current_choice = color_review_dropdown.get()
            option_names = sorted(merged_options)
            color_review_dropdown.configure(values=option_names)

            if current_choice in merged_options:
                color_review_dropdown.set(current_choice)
            else:
                color_review_dropdown.set(displayed_color)

            if source == "Fallback":
                status_text = (
                    "BrickLink colors could not be loaded; "
                    "showing the supported fallback list."
                )
                status_color = "#B45309"
            else:
                status_text = (
                    f"{len(option_names)} known BrickLink colors "
                    f"loaded for Part {part_id}."
                )
                status_color = "#15803D"

            processing_status_label.configure(
                text=status_text,
                foreground=status_color,
            )

    except queue.Empty:
        pass

    window.after(150, check_color_option_queue)


# FUNCTION GUIDE: open_enlarged_preview
# Purpose: Open the entire selected image in a larger, uncropped window.
def open_enlarged_preview(preview_type):
    """Open the entire selected image in a larger, uncropped window."""

    source_image = full_preview_images.get(preview_type)

    if source_image is None:
        processing_status_label.configure(
            text="No image is currently available to enlarge.",
            foreground="#B45309",
        )
        return

    enlarged_window = ctk.CTkToplevel(window)
    enlarged_window.title(
        "Uploaded LEGO Image"
        if preview_type == "uploaded"
        else "BrickLink Catalog Reference"
    )
    enlarged_window.transient(window)
    enlarged_window.configure(fg_color=APP_BACKGROUND)

    screen_width = enlarged_window.winfo_screenwidth()
    screen_height = enlarged_window.winfo_screenheight()
    maximum_width = min(1000, screen_width - 140)
    maximum_height = min(760, screen_height - 190)

    enlarged_image = source_image.copy()
    enlarged_image.thumbnail(
        (maximum_width, maximum_height),
        Image.Resampling.LANCZOS,
    )
    displayed_image = ImageTk.PhotoImage(enlarged_image)

    image_label = tk.Label(
        enlarged_window,
        image=displayed_image,
        background="white",
    )
    image_label.image = displayed_image
    image_label.pack(padx=18, pady=(18, 8))

    ctk.CTkLabel(
        enlarged_window,
        text="Entire image shown • scaled proportionally • nothing cropped",
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(family="Segoe UI", size=11),
    ).pack(pady=(0, 10))

    ctk.CTkButton(
        enlarged_window,
        text="Close",
        command=enlarged_window.destroy,
        width=110,
        fg_color=SECONDARY_BUTTON,
        hover_color=SECONDARY_BUTTON_HOVER,
    ).pack(pady=(0, 16))

    enlarged_window.bind(
        "<Escape>",
        lambda _event: enlarged_window.destroy(),
    )
    enlarged_window.focus_force()


# FUNCTION GUIDE: display_uploaded_image
# Purpose: Display one selected image in the uploaded-image preview.
def display_uploaded_image(image_path):
    """Display one selected image in the uploaded-image preview."""

    with Image.open(image_path) as source_image:
        selected_image = source_image.copy()

    full_preview_images["uploaded"] = selected_image.copy()
    selected_image.thumbnail(
        (190, 190),
        Image.Resampling.LANCZOS,
    )
    displayed_image = ImageTk.PhotoImage(selected_image)

    uploaded_image_preview.configure(
        image=displayed_image,
        text="",
    )

    # Keep the image in memory while it is displayed.
    uploaded_image_preview.image = displayed_image

    selected_file_label.configure(
        text=f"{Path(image_path).name}  •  Click image to enlarge",
    )


# FUNCTION GUIDE: display_reference_image
# Purpose: Download and display the matching BrickLink catalog image.
def display_reference_image(result):
    """Load the matching BrickLink image without blocking navigation."""

    reference_url = result.get("Reference Image URL", "")

    if not reference_url:
        full_preview_images["reference"] = None
        reference_image_preview.configure(
            image="",
            text="No catalog reference image available.",
        )
        reference_image_preview.image = None
        return

    if reference_url in reference_image_cache:
        _show_reference_pil(reference_image_cache[reference_url])
        return

    if reference_url in reference_image_failures:
        full_preview_images["reference"] = None
        reference_image_preview.configure(image="", text="Catalog reference image unavailable")
        reference_image_preview.image = None
        return

    full_preview_images["reference"] = None
    reference_image_preview.configure(image="", text="Loading catalog reference…")
    reference_image_preview.image = None

    request_reference_image(reference_url)


def request_reference_image(reference_url):
    """Share downloads between the selected preview, gallery, and prefetch."""
    if (not reference_url or reference_url in reference_image_cache
            or reference_url in reference_image_loading or reference_url in reference_image_failures):
        return
    reference_image_loading.add(reference_url)

    def download():
        try:
            response = requests.get(
                reference_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0",
                    "Referer": "https://www.bricklink.com/",
                    "Accept": "image/*",
                },
                timeout=15,
            )
            response.raise_for_status()
            with Image.open(BytesIO(response.content)) as source_image:
                downloaded = source_image.copy()
            reference_image_queue.put((reference_url, downloaded, None))
        except (requests.RequestException, ValueError, OSError) as error:
            reference_image_queue.put((reference_url, None, error))

    threading.Thread(target=download, daemon=True).start()


def _show_reference_pil(reference_image):
    """Display a downloaded catalog image on Tkinter's main thread."""
    full_preview_images["reference"] = reference_image.copy()
    preview = reference_image.copy()
    preview.thumbnail((190, 190), Image.Resampling.LANCZOS)
    displayed = ImageTk.PhotoImage(preview)
    reference_image_preview.configure(image=displayed, text="")
    reference_image_preview.image = displayed


def check_reference_image_queue():
    """Apply completed reference downloads only to the still-selected row."""
    try:
        while True:
            reference_url, downloaded, error = reference_image_queue.get_nowait()
            reference_image_loading.discard(reference_url)
            if downloaded is not None:
                reference_image_cache[reference_url] = downloaded
            else:
                reference_image_failures.add(reference_url)
            selected = image_listbox.curselection()
            if not selected:
                continue
            current_path = str(Path(visible_image_paths[selected[0]]))
            current = processed_results.get(current_path, {})
            if current.get("Reference Image URL") != reference_url:
                continue
            if downloaded is not None:
                _show_reference_pil(downloaded)
            else:
                print(f"Reference image error: {error}")
                full_preview_images["reference"] = None
                reference_image_preview.configure(image="", text="Catalog reference image\ncould not be loaded.")
                reference_image_preview.image = None
    except queue.Empty:
        pass
    window.after(100, check_reference_image_queue)


# FUNCTION GUIDE: display_review_badge
# Purpose: Present a review decision as a colored status badge.
def display_review_badge(badge, review_value):
    """Present a review decision as a colored status badge."""

    if review_value == "Yes":
        badge.configure(
            text="YES",
            fg_color=REVIEW_BADGE_BACKGROUND,
            text_color=REVIEW_BADGE_TEXT,
        )

    elif review_value == "No":
        badge.configure(
            text="NO",
            fg_color=CLEAR_BADGE_BACKGROUND,
            text_color=CLEAR_BADGE_TEXT,
        )

    else:
        badge.configure(
            text="-",
            fg_color=NEUTRAL_BADGE_BACKGROUND,
            text_color=NEUTRAL_BADGE_TEXT,
        )


# FUNCTION GUIDE: display_confidence_badge
# Purpose: Present a confidence percentage as a colored badge.
def display_confidence_badge(badge, confidence_value):
    """Present a confidence percentage as a colored badge."""

    try:
        confidence_number = float(
            str(confidence_value).replace("%", "")
        )

    except (TypeError, ValueError):
        badge.configure(
            text="-",
            fg_color=NEUTRAL_BADGE_BACKGROUND,
            text_color=NEUTRAL_BADGE_TEXT,
        )
        return

    if confidence_number >= 90:
        badge.configure(
            text=f"{confidence_number:.2f}%",
            fg_color=CLEAR_BADGE_BACKGROUND,
            text_color=CLEAR_BADGE_TEXT,
        )

    elif confidence_number >= 80:
        badge.configure(
            text=f"{confidence_number:.2f}%",
            fg_color=REVIEW_BADGE_BACKGROUND,
            text_color=REVIEW_BADGE_TEXT,
        )

    else:
        badge.configure(
            text=f"{confidence_number:.2f}%",
            fg_color=LOW_CONFIDENCE_BACKGROUND,
            text_color=LOW_CONFIDENCE_TEXT,
        )

# FUNCTION GUIDE: display_result_details
# Purpose: Display the selected image's saved identification details.
def display_result_details(result):
    """Display the selected image's saved identification details."""

    part_name_value.configure(
        text=result.get("Part Name", "Unknown")
    )

    part_id_value.configure(
        text=result.get("Part ID", "Unknown")
    )

    lego_color_value.configure(
        text=result.get("LEGO Color", "Unknown")
    )

    display_confidence_badge(
        part_confidence_value,
        result.get("Part Confidence", "Unknown"),
    )

    display_confidence_badge(
        color_confidence_value,
        result.get("Color Confidence", "Unknown"),
    )

    display_review_badge(
        part_review_value,
        result.get("Part Review Required", "Unknown"),
    )

    display_review_badge(
        color_review_value,
        result.get("Color Review Required", "Unknown"),
    )

    color_review_controls.grid_remove()
    rescan_part_button.grid_remove()
    part_save_status_label.grid_remove()

    # Color verification is available for every processed result. A green
    # prediction can still be corrected by an employee when the photograph
    # and BrickLink reference show that the automatic color was wrong.
    # Color verification remains an optional employee action for every
    # processed result, including after an earlier correction was saved.
    if result:
        displayed_color = result.get("LEGO Color", "Unknown")
        image_name = result.get("Image", "Unknown")

        # Use cached part-specific choices immediately when available.
        # Otherwise show the prediction while BrickLink loads in the
        # background, rather than displaying every possible LEGO color.
        saved_options = dict(color_options_by_image.get(image_name) or {})

        if saved_options:
            displayed_color_id = (
                result.get("Confirmed BrickLink Color ID")
                or result.get("BrickLink Color ID")
                or BRICKLINK_REVIEW_COLORS.get(displayed_color)
            )
            if displayed_color_id:
                saved_options[displayed_color] = displayed_color_id
                color_options_by_image[image_name] = saved_options
            available_colors = sorted(saved_options)
        else:
            # Show the current color while the full part-specific list loads,
            # but do not cache this one-item placeholder as a finished lookup.
            available_colors = [displayed_color]
            request_part_color_options(result)

        color_review_dropdown.configure(
            values=available_colors
        )
        color_review_dropdown.set(displayed_color)

        color_review_controls.configure(fg_color="#ECFDF5")
        color_review_prompt.configure(
            text="Verify or Change Color",
            text_color="#166534",
        )
        confirm_color_button.configure(text="Save Color")
        if result.get("Color Review Completed") == "Yes":
            color_save_status_label.configure(
                text=f"✓ Saved: {displayed_color}",
                text_color="#15803D",
            )
        else:
            color_save_status_label.configure(
                text="Choose a color, then save",
                text_color=TEXT_SECONDARY,
            )

        color_review_controls.grid_remove()

    # Rescanning is always an optional employee action for processed parts.
    rescan_part_button.configure(
        text="Rescan Part",
        command=select_rescan_image,
        state="normal",
        fg_color=SECONDARY_BUTTON,
        hover_color=SECONDARY_BUTTON_HOVER,
    )

    rescan_part_button.grid(
        row=2,
        column=0,
        columnspan=2,
        padx=(20, 8),
        pady=(0, 8),
        sticky="w",
    )

    if result.get("Part Review Completed") == "Yes":
        part_save_status_label.configure(
            text=f"✓ Saved: Part ID {result.get('Part ID', 'Unknown')}",
            text_color="#15803D",
        )
    else:
        part_save_status_label.configure(
            text="Rescan available",
            text_color=TEXT_SECONDARY,
        )
    part_save_status_label.grid(
        row=2,
        column=2,
        columnspan=4,
        padx=(0, 8),
        pady=(0, 8),
        sticky="w",
    )



# FUNCTION GUIDE: _next_captured_image_path
# Purpose: Return the next available ``lego_XX.jpg`` capture path.
def _next_captured_image_path():
    """Return the next available ``lego_XX.jpg`` capture path."""

    # Captures live with the active results dataset. Archiving a completed LEGO
    # set therefore carries its alternate-angle photographic evidence with it.
    CAPTURED_IMAGES_FOLDER.mkdir(parents=True, exist_ok=True)
    image_number_pattern = re.compile(
        r"^lego_(\d+)\.(?:jpg|jpeg|png)$",
        re.IGNORECASE,
    )
    highest_number = 0
    known_paths = [
        *CAPTURED_IMAGES_FOLDER.glob("lego_*"),
        *(Path(path) for path in selected_image_paths),
    ]

    for image_path in known_paths:
        match = image_number_pattern.match(Path(image_path).name)

        if match:
            highest_number = max(
                highest_number,
                int(match.group(1)),
            )

    return CAPTURED_IMAGES_FOLDER / f"lego_{highest_number + 1:02d}.jpg"


def _add_captured_image_to_batch(image_path):
    """Add a newly captured standalone part image to the active queue."""

    normalized = str(Path(image_path))
    if normalized in {str(Path(path)) for path in selected_image_paths}:
        return

    selected_image_paths.append(normalized)
    visible_image_paths.clear()
    visible_image_paths.extend(selected_image_paths)
    image_filter_dropdown.set("All Images")

    image_listbox.delete(0, tk.END)
    for queued_path in selected_image_paths:
        set_image_list_row(image_listbox.size(), queued_path)

    image_count_label.configure(
        text=f"{len(selected_image_paths)} image(s) selected"
    )
    batch_progress["maximum"] = max(len(selected_image_paths), 1)
    new_index = len(selected_image_paths) - 1
    image_listbox.selection_set(new_index)
    image_listbox.activate(new_index)
    image_listbox.see(new_index)
    update_batch_summary()
    show_selected_image()
    processing_status_label.configure(
        text=(
            f"Camera image {Path(image_path).name} added. "
            "Click Process All Images when ready."
        ),
        foreground="#15803D",
    )


def open_add_image_camera():
    """Open a reusable camera view and add its accepted photo as a new part."""

    try:
        import cv2 as camera_cv2
    except ImportError:
        messagebox.showerror(
            "Camera Support Missing",
            "Camera support is not installed. Use Browse instead, or install the project requirements.",
            parent=window,
        )
        return

    camera_window = ctk.CTkToplevel(window)
    camera_window.title("Add New Part Image")
    fit_window_to_screen(
        camera_window, 700, 600, minimum_width=620, minimum_height=520
    )
    camera_window.transient(window)
    camera_window.grab_set()

    ctk.CTkLabel(
        camera_window,
        text="Photograph One LEGO Part",
        font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
        text_color=TEXT_PRIMARY,
    ).pack(pady=(18, 4))
    ctk.CTkLabel(
        camera_window,
        text="Capture one part at a time. After adding it, reopen Add Images to photograph another.",
        wraplength=610,
        text_color=TEXT_SECONDARY,
    ).pack(pady=(0, 12))

    preview = ctk.CTkLabel(
        camera_window,
        text="Opening camera…",
        width=560,
        height=350,
        corner_radius=10,
        fg_color="#E8EDF2",
        text_color=TEXT_SECONDARY,
    )
    preview.pack(padx=30, pady=(0, 12), fill="both", expand=True)
    controls = ttk.Frame(camera_window)
    controls.pack(pady=(0, 18))

    state = {"camera": None, "after_id": None, "frame": None, "captured": None}

    def stop_camera():
        after_id = state.get("after_id")
        if after_id:
            try:
                camera_window.after_cancel(after_id)
            except tk.TclError:
                pass
            state["after_id"] = None
        camera = state.get("camera")
        if camera is not None:
            camera.release()
            state["camera"] = None

    def close_camera():
        stop_camera()
        camera_window.destroy()

    def update_preview():
        camera = state.get("camera")
        if camera is None or not camera_window.winfo_exists():
            return
        success, frame = camera.read()
        if success:
            state["frame"] = frame.copy()
            rgb = camera_cv2.cvtColor(frame, camera_cv2.COLOR_BGR2RGB)
            source = Image.fromarray(rgb)
            display = ctk.CTkImage(
                light_image=source, dark_image=source, size=(560, 350)
            )
            preview.configure(image=display, text="")
            preview.image = display
            capture_button.configure(state="normal")
        state["after_id"] = camera_window.after(40, update_preview)

    def start_camera():
        stop_camera()
        state["captured"] = None
        backend = camera_cv2.CAP_DSHOW if os.name == "nt" else camera_cv2.CAP_ANY
        camera = camera_cv2.VideoCapture(0, backend)
        if not camera.isOpened():
            camera.release()
            messagebox.showerror(
                "Camera Unavailable",
                "The camera could not be opened. Check that another program is not using it.",
                parent=camera_window,
            )
            return
        state["camera"] = camera
        capture_button.configure(state="disabled")
        use_button.configure(state="disabled")
        update_preview()

    def capture_photo():
        frame = state.get("frame")
        if frame is None:
            return
        state["captured"] = frame.copy()
        stop_camera()
        capture_button.configure(state="disabled")
        use_button.configure(state="normal")

    def use_photo():
        frame = state.get("captured")
        if frame is None:
            return
        captured_path = _next_captured_image_path()
        rgb = camera_cv2.cvtColor(frame, camera_cv2.COLOR_BGR2RGB)
        Image.fromarray(rgb).save(captured_path, format="JPEG", quality=95)
        close_camera()
        _add_captured_image_to_batch(captured_path)
        show_save_confirmation(f"Camera photo {captured_path.name}")

    capture_button = ttk.Button(
        controls, text="Capture", command=capture_photo, state="disabled"
    )
    capture_button.pack(side="left", padx=4)
    ttk.Button(controls, text="Retake", command=start_camera).pack(side="left", padx=4)
    use_button = ttk.Button(
        controls, text="Use This Photo", command=use_photo, state="disabled"
    )
    use_button.pack(side="left", padx=4)
    ttk.Button(controls, text="Cancel", command=close_camera).pack(side="left", padx=4)

    camera_window.protocol("WM_DELETE_WINDOW", close_camera)
    camera_window.after(100, start_camera)


def add_part_images():
    """Choose between a new camera photo and browsing for part images."""

    if process_button.cget("state") == "disabled":
        messagebox.showwarning(
            "Processing in Progress",
            "Please wait until the current processing run is complete.",
            parent=window,
        )
        return

    chooser = ctk.CTkToplevel(window)
    chooser.title("Add Part Images")
    fit_window_to_screen(chooser, 390, 185, minimum_width=390, minimum_height=185)
    chooser.resizable(False, False)
    chooser.transient(window)
    chooser.grab_set()
    ctk.CTkLabel(
        chooser,
        text="Add another part image",
        font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
        text_color=TEXT_PRIMARY,
    ).pack(pady=(20, 14))
    actions = ttk.Frame(chooser)
    actions.pack()

    def choose_camera():
        chooser.destroy()
        open_add_image_camera()

    def choose_files():
        chooser.destroy()
        upload_images()

    ttk.Button(actions, text="Open Camera", command=choose_camera).pack(side="left", padx=6)
    ttk.Button(actions, text="Browse for Images", command=choose_files).pack(side="left", padx=6)
    ttk.Button(chooser, text="Cancel", command=chooser.destroy).pack(pady=14)


# FUNCTION GUIDE: _accept_rescan_image
# Purpose: Store an alternate image choice and immediately process it.
def _accept_rescan_image(
    original_image_path,
    rescan_image_path,
    source_window,
):
    """Store an alternate image choice and immediately process it."""

    pending_rescan_images[str(original_image_path)] = str(
        rescan_image_path
    )
    source_window.destroy()

    part_save_status_label.configure(
        text=(
            f"Photo selected: {Path(rescan_image_path).name} - "
            "comparison pending"
        ),
        text_color="#B45309",
    )

    processing_status_label.configure(
        text=(
            f"Alternate image selected: "
            f"{Path(rescan_image_path).name}"
        ),
        foreground="#1D4ED8",
    )
    window.after(50, process_selected_rescan)


# FUNCTION GUIDE: select_rescan_image
# Purpose: Open a camera-first rescan window with an upload alternative.
def select_rescan_image():
    """Open a camera-first rescan window with an upload alternative."""

    selected_indices = image_listbox.curselection()

    if not selected_indices:
        processing_status_label.configure(
            text="Please select a processed part to rescan.",
            foreground="#B45309",
        )
        return

    selected_index = selected_indices[0]
    original_image_path = Path(
        visible_image_paths[selected_index]
    )

    saved_result = processed_results.get(str(original_image_path))

    source_window = ctk.CTkToplevel(window)
    source_window.title("Rescan Part")
    fit_window_to_screen(
        source_window,
        700,
        600,
        minimum_width=620,
        minimum_height=520,
    )
    source_window.transient(window)
    source_window.grab_set()

    ctk.CTkLabel(
        source_window,
        text="Capture Another Angle",
        font=ctk.CTkFont(
            family="Segoe UI",
            size=24,
            weight="bold",
        ),
        text_color=TEXT_PRIMARY,
    ).pack(pady=(24, 4))

    ctk.CTkLabel(
        source_window,
        text=(
            "Take a new photo with the connected camera, or upload "
            "an existing image. If the first scan had no match, a "
            "successful rescan becomes its identification."
        ),
        wraplength=620,
        justify="center",
        text_color=TEXT_SECONDARY,
    ).pack(pady=(0, 16))

    preview_label = ctk.CTkLabel(
        source_window,
        text="Camera preview will appear here",
        width=560,
        height=350,
        corner_radius=10,
        fg_color="#E8EDF2",
        text_color=TEXT_SECONDARY,
    )
    preview_label.pack(padx=30, pady=(0, 14), fill="both", expand=True)

    action_frame = ctk.CTkFrame(
        source_window,
        fg_color="transparent",
    )
    action_frame.pack(pady=(0, 20))

    camera_state = {
        "capture": None,
        "after_id": None,
        "latest_frame": None,
        "captured_frame": None,
    }

    # FUNCTION GUIDE: stop_camera
    # Purpose: Release the camera and cancel its scheduled preview update.
    def stop_camera():
        """Release the camera and cancel its scheduled preview update."""

        after_id = camera_state.get("after_id")

        if after_id:
            try:
                source_window.after_cancel(after_id)
            except tk.TclError:
                pass
            camera_state["after_id"] = None

        camera = camera_state.get("capture")

        if camera is not None:
            camera.release()
            camera_state["capture"] = None

    # FUNCTION GUIDE: close_source_window
    # Purpose: Release the camera and close the rescan source window.
    def close_source_window():
        """Release the camera and close the rescan source window."""
        stop_camera()
        source_window.destroy()

    # FUNCTION GUIDE: update_camera_preview
    # Purpose: Read camera frames and refresh the live preview safely.
    def update_camera_preview():
        """Read camera frames and refresh the live preview safely."""
        camera = camera_state.get("capture")

        if camera is None or not source_window.winfo_exists():
            return

        success, frame = camera.read()

        if success:
            camera_state["latest_frame"] = frame.copy()
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            preview_image = Image.fromarray(rgb_frame)
            display_image = ctk.CTkImage(
                light_image=preview_image,
                dark_image=preview_image,
                size=(560, 350),
            )
            preview_label.configure(image=display_image, text="")
            preview_label.image = display_image
            capture_button.configure(state="normal")

        camera_state["after_id"] = source_window.after(
            40,
            update_camera_preview,
        )

    # FUNCTION GUIDE: start_camera
    # Purpose: Start the first available connected camera.
    def start_camera():
        """Start the first available connected camera."""

        nonlocal cv2

        try:
            import cv2 as imported_cv2
            cv2 = imported_cv2
        except ImportError:
            messagebox.showerror(
                "Camera Support Missing",
                (
                    "Camera support is not installed. Run "
                    "pip install -r requirements.txt, then restart "
                    "the application."
                ),
                parent=source_window,
            )
            return

        stop_camera()
        camera_state["captured_frame"] = None
        camera_backend = (
            cv2.CAP_DSHOW
            if os.name == "nt"
            else cv2.CAP_ANY
        )
        camera = cv2.VideoCapture(0, camera_backend)

        if not camera.isOpened():
            camera.release()
            messagebox.showerror(
                "Camera Unavailable",
                (
                    "The connected camera could not be opened. Check "
                    "that it is connected and not being used by another "
                    "application, or choose Upload Existing Photo."
                ),
                parent=source_window,
            )
            return

        camera_state["capture"] = camera
        if not take_photo_button.winfo_manager():
            take_photo_button.pack(
                side="left",
                padx=6,
                before=upload_button,
            )
        if not capture_button.winfo_manager():
            capture_button.pack(
                side="left",
                padx=6,
                before=upload_button,
            )
        take_photo_button.configure(
            text="Camera Active",
            state="disabled",
        )
        capture_button.configure(state="disabled")
        retake_button.pack_forget()
        use_photo_button.pack_forget()
        update_camera_preview()

    # FUNCTION GUIDE: capture_current_frame
    # Purpose: Freeze the current camera frame for employee confirmation.
    def capture_current_frame():
        """Freeze the current camera frame for employee confirmation."""
        frame = camera_state.get("latest_frame")

        if frame is None:
            return

        camera_state["captured_frame"] = frame.copy()
        stop_camera()
        take_photo_button.pack_forget()
        capture_button.pack_forget()
        retake_button.pack(side="left", padx=6)
        use_photo_button.pack(side="left", padx=6)
        processing_status_label.configure(
            text="Photo captured. Choose Use This Photo or Retake.",
            foreground="#1D4ED8",
        )

    # FUNCTION GUIDE: use_captured_photo
    # Purpose: Save the confirmed camera frame and begin comparison.
    def use_captured_photo():
        """Save the confirmed camera frame and begin comparison."""
        frame = camera_state.get("captured_frame")

        if frame is None:
            return

        captured_path = _next_captured_image_path()
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        Image.fromarray(rgb_frame).save(
            captured_path,
            format="JPEG",
            quality=95,
        )
        show_save_confirmation(
            f"Camera photo {captured_path.name}"
        )
        _accept_rescan_image(
            original_image_path,
            captured_path,
            source_window,
        )

    # FUNCTION GUIDE: upload_existing_photo
    # Purpose: Choose an existing alternate-angle image instead of the camera.
    def upload_existing_photo():
        """Choose an existing alternate-angle image instead of the camera."""
        rescan_image_path = filedialog.askopenfilename(
            parent=source_window,
            title="Choose LEGO Image for Rescan",
            initialdir=DEFAULT_IMAGE_FOLDER,
            filetypes=[
                ("Image Files", "*.jpg *.jpeg *.png"),
                ("JPEG Files", "*.jpg *.jpeg"),
                ("PNG Files", "*.png"),
            ],
        )

        if rescan_image_path:
            stop_camera()
            _accept_rescan_image(
                original_image_path,
                rescan_image_path,
                source_window,
            )

    # Assigned during start_camera; keeping the name in the closure avoids
    # requiring OpenCV merely to launch the application.
    cv2 = None

    take_photo_button = ctk.CTkButton(
        action_frame,
        text="Take Photo Now",
        command=start_camera,
        width=150,
        fg_color=PRIMARY_GREEN,
        hover_color=PRIMARY_GREEN_HOVER,
    )
    take_photo_button.pack(side="left", padx=6)

    capture_button = ctk.CTkButton(
        action_frame,
        text="Capture Photo",
        command=capture_current_frame,
        state="disabled",
        width=140,
        fg_color=SECONDARY_BUTTON,
        hover_color=SECONDARY_BUTTON_HOVER,
    )
    capture_button.pack(side="left", padx=6)

    upload_button = ctk.CTkButton(
        action_frame,
        text="Upload Existing Photo",
        command=upload_existing_photo,
        width=175,
        fg_color="#596873",
        hover_color="#45525B",
    )
    upload_button.pack(side="left", padx=6)

    retake_button = ctk.CTkButton(
        action_frame,
        text="Retake",
        command=start_camera,
        width=100,
        fg_color="#596873",
        hover_color="#45525B",
    )

    use_photo_button = ctk.CTkButton(
        action_frame,
        text="Use This Photo",
        command=use_captured_photo,
        width=140,
        fg_color=PRIMARY_GREEN,
        hover_color=PRIMARY_GREEN_HOVER,
    )

    source_window.protocol("WM_DELETE_WINDOW", close_source_window)


# FUNCTION GUIDE: run_part_rescan
# Purpose: Process an alternate-angle image in the background.
def run_part_rescan(
    original_image_path,
    rescan_image_path,
):
    """
    Process an alternate-angle image in the background.

    The result is placed into rescan_queue so the worker thread
    does not modify GUI widgets directly.
    """

    try:
        rescan_result = identify_part(
            Path(rescan_image_path)
        )

        if rescan_result is None:
            rescan_queue.put(
                (
                    "failed",
                    original_image_path,
                )
            )
            return

        rescan_queue.put(
            (
                "complete",
                original_image_path,
                rescan_image_path,
                rescan_result,
            )
        )

    except Exception as error:
        print(f"Part rescan error: {error}")

        rescan_queue.put(
            (
                "failed",
                original_image_path,
            )
        )


# FUNCTION GUIDE: process_selected_rescan
# Purpose: Start processing the selected alternate-angle image.
def process_selected_rescan():
    """Start processing the selected alternate-angle image."""

    selected_indices = image_listbox.curselection()

    if not selected_indices:
        processing_status_label.configure(
            text="Please select a processed part to rescan.",
            foreground="#B45309",
        )
        return

    selected_index = selected_indices[0]

    original_image_path = str(
        Path(visible_image_paths[selected_index])
    )

    rescan_image_path = pending_rescan_images.get(
        original_image_path
    )

    if not rescan_image_path:
        processing_status_label.configure(
            text="Please select an image for the rescan first.",
            foreground="#B45309",
        )
        return

    rescan_part_button.configure(
        text="Processing...",
        state="disabled",
    )

    processing_status_label.configure(
        text=(
            f"Processing alternate image: "
            f"{Path(rescan_image_path).name}"
        ),
        foreground="#1D4ED8",
    )

    worker = threading.Thread(
        target=run_part_rescan,
        args=(
            original_image_path,
            rescan_image_path,
        ),
        daemon=True,
    )
    worker.start()        

# FUNCTION GUIDE: check_rescan_queue
# Purpose: Check whether the alternate-angle processing has finished.
def check_rescan_queue():
    """Check whether the alternate-angle processing has finished."""

    try:
        message = rescan_queue.get_nowait()

    except queue.Empty:
        window.after(100, check_rescan_queue)
        return

    message_type = message[0]

    if message_type == "failed":
        rescan_part_button.configure(
            text="Rescan Part",
            state="normal",
            command=select_rescan_image,
            fg_color=SECONDARY_BUTTON,
            hover_color=SECONDARY_BUTTON_HOVER,
        )

        processing_status_label.configure(
            text="The alternate image could not be processed.",
            foreground="#B91C1C",
        )
        window.after(100, check_rescan_queue)
        return

    if message_type == "complete":
        (
            _,
            original_image_path,
            rescan_image_path,
            rescan_result,
        ) = message

        # Keep the rescan result separate from the original result.
        completed_rescans[original_image_path] = {
            "Image Path": rescan_image_path,
            "Result": rescan_result,
        }

        original_result = processed_results.get(
            original_image_path,
            {},
        )

        # When the first scan returned no match there is nothing to compare.
        # Promote the successful rescan to the original row and save it using
        # the original crop's stable filename.
        if not original_result:
            promoted_result = dict(rescan_result)
            promoted_result["Image"] = Path(original_image_path).name
            processed_results[original_image_path] = promoted_result
            newly_processed_paths.add(original_image_path)
            append_result_to_csv(promoted_result)
            pending_rescan_images.pop(original_image_path, None)
            completed_rescans.pop(original_image_path, None)

            selected_indices = image_listbox.curselection()
            if selected_indices:
                selected_index = selected_indices[0]
                set_image_list_row(
                    selected_index,
                    visible_image_paths[selected_index],
                )
                image_listbox.selection_set(selected_index)

            display_reference_image(promoted_result)
            display_result_details(promoted_result)
            update_batch_summary()
            rescan_part_button.configure(
                text="Rescan Part",
                command=select_rescan_image,
                state="normal",
                fg_color=SECONDARY_BUTTON,
                hover_color=SECONDARY_BUTTON_HOVER,
            )
            processing_status_label.configure(
                text=(
                    f"Rescan identified Part ID "
                    f"{promoted_result.get('Part ID', 'Unknown')} and saved it."
                ),
                foreground="#15803D",
            )
            show_save_confirmation(
                f"Rescan result for {promoted_result['Image']}"
            )
            window.after(100, check_rescan_queue)
            return

        original_part_id = original_result.get("Part ID")
        rescan_part_id = rescan_result.get("Part ID")

        if original_part_id == rescan_part_id:
            comparison_text = (
                f"Rescan matched Part ID {rescan_part_id}."
            )
            status_color = "#15803D"

        else:
            comparison_text = (
                f"Rescan returned Part ID {rescan_part_id}; "
                f"original was {original_part_id}."
            )
            status_color = "#B45309"

        rescan_part_button.configure(
            text="Compare Results",
            command=show_rescan_comparison,
            state="normal",
            fg_color=SECONDARY_BUTTON,
            hover_color=SECONDARY_BUTTON_HOVER,
        )

        processing_status_label.configure(
            text=comparison_text,
            foreground=status_color,
        )
        window.after(100, check_rescan_queue)
# FUNCTION GUIDE: complete_part_review
# Purpose: Save and apply the employee's part-review decision.
def complete_part_review(
    original_image_path,
    decision,
    comparison_window,
):
    """Save and apply the employee's part-review decision."""

    original_result = processed_results.get(
        original_image_path
    )

    completed_rescan = completed_rescans.get(
        original_image_path
    )

    if not original_result or not completed_rescan:
        processing_status_label.configure(
            text="The part-review results are unavailable.",
            foreground="#B91C1C",
        )
        return

    rescan_result = completed_rescan["Result"]
    rescan_image_path = completed_rescan["Image Path"]

    try:
        save_part_review(
            original_result,
            rescan_result,
            rescan_image_path,
            decision,
        )

    except OSError as error:
        print(f"Part review save error: {error}")

        processing_status_label.configure(
            text="The part-review decision could not be saved.",
            foreground="#B91C1C",
        )
        return

    if decision == "Use Rescan Result":
        # The employee chose the new photograph's analysis, so adopt its part,
        # color, reference, and confidence fields together. Retaining the old
        # scan's color made the screen and saved CSV internally inconsistent.
        replacement_fields = (
            "Part ID",
            "Part Name",
            "Type",
            "Part Confidence",
            "LEGO Color",
            "BrickLink Color ID",
            "Predicted LEGO Color",
            "Predicted BrickLink Color ID",
            "Color Confidence",
            "Color Review Required",
            "Color Approval Status",
            "Color Method",
            "Estimated RGB",
            "Color Distance",
            "Bounding Box Score",
            "Reference Image URL",
            "BrickLink URL",
        )
        for field_name in replacement_fields:
            if field_name in rescan_result:
                original_result[field_name] = rescan_result[field_name]

        original_result.pop("Confirmed LEGO Color", None)
        original_result.pop("Confirmed BrickLink Color ID", None)
        original_result["Color Review Completed"] = "No"
        clear_color_review(original_result.get("Image", ""))
        color_options_by_image.pop(original_result.get("Image", ""), None)

    original_result["Part Review Required"] = "No"
    original_result["Part Review Completed"] = "Yes"
    original_result["Part Review Decision"] = decision
    original_result["Part Approval Status"] = (
        "Corrected" if decision == "Use Rescan Result" else "Confirmed"
    )

    queue_csv_review(original_result)

    # Refresh the main result display.
    if decision == "Use Rescan Result":
        display_uploaded_image(rescan_image_path)
    display_reference_image(original_result)
    display_result_details(original_result)

    selected_indices = image_listbox.curselection()

    if selected_indices:
        selected_index = selected_indices[0]
        image_name = Path(
            visible_image_paths[selected_index]
        ).name

        set_image_list_row(
            selected_index,
            visible_image_paths[selected_index],
        )
        image_listbox.selection_set(selected_index)
        image_listbox.activate(selected_index)

    comparison_window.destroy()

    update_batch_summary()
    apply_image_filter()

    rescan_part_button.configure(
        text="✓ Approved",
        state="disabled",
    )
    part_save_status_label.configure(
        text=f"✓ Approval queued: Part ID {original_result.get('Part ID', 'Unknown')}",
        text_color="#15803D",
    )

    # FUNCTION GUIDE: restore_rescan_button
    # Purpose: Restore the rescan button after its saved confirmation.
    def restore_rescan_button():
        """Restore the rescan button after its saved confirmation."""
        if rescan_part_button.winfo_exists():
            rescan_part_button.configure(
                text="Rescan Part",
                command=select_rescan_image,
                state="normal",
                fg_color=SECONDARY_BUTTON,
                hover_color=SECONDARY_BUTTON_HOVER,
            )

    window.after(1400, restore_rescan_button)
    show_save_confirmation(
        f"Part decision for {original_result.get('Image', 'image')}"
    )

    processing_status_label.configure(
        text=f"Part review completed: {decision}.",
        foreground="#15803D",
    )


# FUNCTION GUIDE: show_rescan_comparison
# Purpose: Display both scans, both catalog matches, and both result summaries.
def show_rescan_comparison():
    """
    Display both scans, both catalog matches, and both result summaries.

    Showing the employee photos beside the catalog references makes it
    easier to notice when the wrong alternate-angle image was selected.
    """

    selected_indices = image_listbox.curselection()

    if not selected_indices:
        processing_status_label.configure(
            text="Please select the original image first.",
            foreground="#B45309",
        )
        return

    selected_index = selected_indices[0]
    original_image_path = str(
        Path(visible_image_paths[selected_index])
    )

    completed_rescan = completed_rescans.get(
        original_image_path
    )

    if not completed_rescan:
        processing_status_label.configure(
            text="No completed rescan is available.",
            foreground="#B45309",
        )
        return

    original_result = processed_results.get(
        original_image_path,
        {},
    )
    rescan_result = completed_rescan.get(
        "Result",
        {},
    )
    rescan_image_path = completed_rescan.get(
        "Image Path",
        "",
    )

    comparison_window = ctk.CTkToplevel(window)
    comparison_window.title("Compare Part Results")
    fit_window_to_screen(
        comparison_window,
        1000,
        650,
        minimum_width=820,
        minimum_height=540,
    )
    comparison_window.resizable(False, False)
    comparison_window.configure(fg_color=APP_BACKGROUND)

    # Keep the comparison window associated with the main application.
    comparison_window.transient(window)
    comparison_window.grab_set()

    comparison_title = ctk.CTkLabel(
        comparison_window,
        text="Part Review Comparison",
        font=ctk.CTkFont(
            family="Segoe UI",
            size=20,
            weight="bold",
        ),
        text_color=TEXT_PRIMARY,
    )
    comparison_title.pack(pady=(22, 6))

    original_part_id = original_result.get(
        "Part ID",
        "Unknown",
    )
    rescan_part_id = rescan_result.get(
        "Part ID",
        "Unknown",
    )

    if original_part_id == rescan_part_id:
        match_text = "The two scans returned the same Part ID."
        match_color = "#15803D"
    else:
        match_text = "The two scans returned different Part IDs."
        match_color = "#B45309"

    comparison_status = ctk.CTkLabel(
        comparison_window,
        text=match_text,
        font=ctk.CTkFont(
            family="Segoe UI",
            size=12,
            weight="bold",
        ),
        text_color=match_color,
    )
    comparison_status.pack(pady=(0, 16))

    comparison_body = ctk.CTkFrame(
        comparison_window,
        fg_color="transparent",
    )
    comparison_body.pack(
        fill="both",
        expand=True,
        padx=20,
    )

    comparison_body.grid_columnconfigure(0, weight=1)
    comparison_body.grid_columnconfigure(1, weight=1)

    # FUNCTION GUIDE: load_comparison_image
    # Purpose: Return a display-sized image from a local path or catalog URL.
    def load_comparison_image(image_source, is_url=False):
        """Return a display-sized image from a local path or catalog URL."""

        try:
            if is_url:
                response = requests.get(
                    image_source,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 Chrome/150.0 Safari/537.36"
                        ),
                        "Referer": "https://www.bricklink.com/",
                        "Accept": "image/*",
                    },
                    timeout=15,
                )
                response.raise_for_status()
                image_file = BytesIO(response.content)
            else:
                image_file = image_source

            with Image.open(image_file) as source_image:
                preview_image = source_image.copy()

            preview_image.thumbnail((185, 155))
            return ImageTk.PhotoImage(preview_image)

        except (
            requests.RequestException,
            ValueError,
            OSError,
        ) as error:
            print(f"Comparison image error: {error}")
            return None

    # FUNCTION GUIDE: create_image_preview
    # Purpose: Create one labeled image preview with a helpful fallback.
    def create_image_preview(parent, title, image_source, is_url=False):
        """Create one labeled image preview with a helpful fallback."""

        preview_column = ctk.CTkFrame(
            parent,
            fg_color="transparent",
        )
        preview_column.pack(
            side="left",
            fill="both",
            expand=True,
            padx=6,
        )

        ctk.CTkLabel(
            preview_column,
            text=title,
            font=ctk.CTkFont(
                family="Segoe UI",
                size=11,
                weight="bold",
            ),
            text_color=TEXT_SECONDARY,
        ).pack(pady=(0, 6))

        displayed_image = None

        if image_source:
            displayed_image = load_comparison_image(
                image_source,
                is_url=is_url,
            )

        preview_label = ctk.CTkLabel(
            preview_column,
            text=(
                ""
                if displayed_image
                else "Image unavailable"
            ),
            image=displayed_image,
            width=190,
            height=160,
            fg_color="#F8FAFC",
            text_color=TEXT_SECONDARY,
        )
        preview_label.pack(fill="both", expand=True)

        # Tkinter removes an image unless a live reference is retained.
        preview_label.image = displayed_image

    # FUNCTION GUIDE: create_result_card
    # Purpose: Create one visual side of the part-result comparison.
    def create_result_card(
        parent,
        column,
        title,
        image_path,
        result,
    ):
        """Create one visual side of the part-result comparison."""

        card = ctk.CTkFrame(
            parent,
            fg_color=CARD_BACKGROUND,
            corner_radius=10,
            border_width=1,
            border_color="#D9DEE3",
        )
        card.grid(
            row=0,
            column=column,
            padx=8,
            sticky="nsew",
        )

        card_title = ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(
                family="Segoe UI",
                size=15,
                weight="bold",
            ),
            text_color=TEXT_PRIMARY,
        )
        card_title.pack(pady=(14, 8))

        image_name = (
            Path(image_path).name
            if image_path
            else "Image unavailable"
        )
        ctk.CTkLabel(
            card,
            text=image_name,
            font=ctk.CTkFont(
                family="Segoe UI",
                size=11,
            ),
            text_color=TEXT_SECONDARY,
        ).pack(pady=(0, 10))

        preview_frame = ctk.CTkFrame(
            card,
            fg_color="transparent",
        )
        preview_frame.pack(
            fill="x",
            padx=10,
            pady=(0, 12),
        )

        create_image_preview(
            preview_frame,
            "Uploaded Photo",
            image_path,
        )
        create_image_preview(
            preview_frame,
            "BrickLink Match",
            result.get("Reference Image URL", ""),
            is_url=True,
        )

        details = (
            f"Part Name: {result.get('Part Name', 'Unknown')}\n"
            f"Part ID: {result.get('Part ID', 'Unknown')}\n"
            f"Part Confidence: "
            f"{result.get('Part Confidence', 'Unknown')}"
        )

        details_label = ctk.CTkLabel(
            card,
            text=details,
            font=ctk.CTkFont(
                family="Segoe UI",
                size=12,
            ),
            text_color=TEXT_PRIMARY,
            justify="left",
            anchor="w",
            wraplength=430,
        )
        details_label.pack(
            fill="x",
            padx=18,
            pady=(0, 14),
        )

    create_result_card(
        comparison_body,
        0,
        "Original Scan",
        original_image_path,
        original_result,
    )

    create_result_card(
        comparison_body,
        1,
        "Alternate-Angle Scan",
        rescan_image_path,
        rescan_result,
    )

    # FUNCTION GUIDE: try_another_rescan
    # Purpose: Close the comparison and select another image.
    def try_another_rescan():
        """Close the comparison and select another image."""

        pending_rescan_images.pop(
            original_image_path,
            None,
        )
        completed_rescans.pop(
            original_image_path,
            None,
        )

        comparison_window.destroy()

        rescan_part_button.configure(
            text="Rescan Part",
            command=select_rescan_image,
            state="normal",
            fg_color=SECONDARY_BUTTON,
            hover_color=SECONDARY_BUTTON_HOVER,
        )

        window.after(100, select_rescan_image)

    decision_frame = ctk.CTkFrame(
        comparison_window,
        fg_color="transparent",
    )
    decision_frame.pack(pady=18)

    keep_original_button = ctk.CTkButton(
        decision_frame,
        text="Keep Original",
        command=lambda: complete_part_review(
            original_image_path,
            "Keep Original",
            comparison_window,
        ),
        width=130,
        height=36,
        corner_radius=8,
        fg_color=SECONDARY_BUTTON,
        hover_color=SECONDARY_BUTTON_HOVER,
    )
    keep_original_button.pack(
        side="left",
        padx=5,
    )

    use_rescan_button = ctk.CTkButton(
        decision_frame,
        text="Use Rescan Result",
        command=lambda: complete_part_review(
            original_image_path,
            "Use Rescan Result",
            comparison_window,
        ),
        width=145,
        height=36,
        corner_radius=8,
        fg_color=PRIMARY_GREEN,
        hover_color=PRIMARY_GREEN_HOVER,
    )
    use_rescan_button.pack(
        side="left",
        padx=5,
    )

    try_another_button = ctk.CTkButton(
        decision_frame,
        text="Try Another Image",
        command=try_another_rescan,
        width=145,
        height=36,
        corner_radius=8,
        fg_color="#596873",
        hover_color="#45525B",
    )
    try_another_button.pack(
        side="left",
        padx=5,
    )

    
# FUNCTION GUIDE: preview_selected_color
# Purpose: Preview the selected BrickLink color without saving it.
def preview_selected_color(selected_color):
    """
    Preview the selected BrickLink color without saving it.

    The actual result remains unchanged until Confirm Color is clicked.
    """

    confirm_color_button.configure(
        text="Save Color",
        state="normal",
    )
    color_save_status_label.configure(
        text=f"Not saved yet: {selected_color}",
        text_color="#B45309",
    )
    # Show the pending choice in the main details immediately. The suffix
    # distinguishes this preview from a value already committed to disk.
    lego_color_value.configure(text=f"{selected_color} (not saved)")

    selected_indices = image_listbox.curselection()

    if not selected_indices:
        return

    selected_index = selected_indices[0]
    image_path = Path(visible_image_paths[selected_index])

    saved_result = processed_results.get(str(image_path))

    if not saved_result:
        return

    image_name = saved_result.get("Image", image_path.name)
    current_options = color_options_by_image.get(
        image_name,
        BRICKLINK_REVIEW_COLORS,
    )
    selected_color_id = current_options.get(selected_color)

    if not selected_color_id:
        return

    part_id = saved_result.get("Part ID", "")

    # Make a temporary copy so the saved result is not changed.
    preview_result = saved_result.copy()

    preview_result["Reference Image URL"] = (
        "https://img.bricklink.com/ItemImage/PN/"
        f"{selected_color_id}/{part_id}.png"
    )

    display_reference_image(preview_result)

    processing_status_label.configure(
        text=f"Previewing {selected_color}. Click Confirm Color to save.",
        foreground="#1D4ED8",
    )


# FUNCTION GUIDE: confirm_selected_color
# Purpose: Save the selected employee-confirmed LEGO color.
def confirm_selected_color():
    """Save the selected employee-confirmed LEGO color."""

    selected_indices = image_listbox.curselection()

    if not selected_indices:
        processing_status_label.configure(
            text="Please select an image before confirming a color.",
            foreground="#B45309",
        )
        return

    selected_index = selected_indices[0]
    image_path = Path(visible_image_paths[selected_index])

    saved_result = processed_results.get(str(image_path))

    if not saved_result:
        processing_status_label.configure(
            text="This image does not have a processed result.",
            foreground="#B45309",
        )
        return

    confirmed_color = color_review_dropdown.get()
    image_name = saved_result.get("Image", image_path.name)
    current_options = color_options_by_image.get(
        image_name,
        BRICKLINK_REVIEW_COLORS,
    )
    confirmed_color_id = current_options.get(confirmed_color)

    if not confirmed_color_id:
        processing_status_label.configure(
            text="The selected color does not have a BrickLink ID.",
            foreground="#B91C1C",
        )
        return

    try:
        review_record = save_color_review(
            saved_result,
            confirmed_color,
            confirmed_color_id,
        )

    except OSError as error:
        print(f"Color review save error: {error}")

        processing_status_label.configure(
            text="The color review could not be saved.",
            foreground="#B91C1C",
        )
        return

    # Preserve both the original and employee-confirmed colors.
    saved_result["Original LEGO Color"] = review_record[
        "Original Color"
    ]
    saved_result["Confirmed LEGO Color"] = confirmed_color
    saved_result["Confirmed BrickLink Color ID"] = (
        confirmed_color_id
    )
    saved_result["Color Review Completed"] = "Yes"
    saved_result["Color Approval Status"] = review_record.get(
        "Color Approval Status",
        "Confirmed",
    )

    # The displayed final color is now the confirmed selection.
    saved_result["LEGO Color"] = confirmed_color
    saved_result["Color Review Required"] = "No"

    # Update the reference image to use the confirmed color.
    part_id = saved_result.get("Part ID", "")

    saved_result["Reference Image URL"] = (
        "https://img.bricklink.com/ItemImage/PN/"
        f"{confirmed_color_id}/{part_id}.png"
    )

    # Update the BrickLink button to open the confirmed color.
    bricklink_url = saved_result.get("BrickLink URL", "")

    if bricklink_url and bricklink_url != "N/A":
        base_url = bricklink_url.split("&idColor=")[0]

        saved_result["BrickLink URL"] = (
            f"{base_url}&idColor={confirmed_color_id}"
        )

    queue_csv_review(saved_result)

    # Refresh the selected result on screen.
    display_reference_image(saved_result)
    display_result_details(saved_result)

    image_name = image_path.name

    set_image_list_row(
        selected_index,
        image_path,
    )

    image_listbox.selection_set(selected_index)
    image_listbox.activate(selected_index)

    update_batch_summary()
    apply_image_filter()

    confirm_color_button.configure(
        text="✓ Approved",
        state="disabled",
    )
    color_save_status_label.configure(
        text=f"✓ Approval queued: {confirmed_color}",
        text_color="#15803D",
    )

    # FUNCTION GUIDE: restore_save_button
    # Purpose: Restore the color-save button after its saved confirmation.
    def restore_save_button():
        """Restore the color-save button after its saved confirmation."""
        if confirm_color_button.winfo_exists():
            confirm_color_button.configure(
                text="Save Color",
                state="normal",
            )

    window.after(1400, restore_save_button)
    show_save_confirmation(
        f"Color {confirmed_color} for {image_name}"
    )

    processing_status_label.configure(
        text=f"{image_name}: {confirmed_color} confirmed.",
        foreground="#15803D",
    )


# FUNCTION GUIDE: open_bricklink
# Purpose: Open the selected result's BrickLink catalog page.
def open_bricklink():
    """Open the selected result's BrickLink catalog page."""

    selected_indices = image_listbox.curselection()

    if not selected_indices:
        processing_status_label.configure(
            text="Please select a processed image first.",
            foreground="#B45309",
        )
        return

    selected_index = selected_indices[0]
    image_path = Path(visible_image_paths[selected_index])

    saved_result = processed_results.get(
        str(image_path)
    )

    if not saved_result:
        processing_status_label.configure(
            text="This image has not been processed.",
            foreground="#B45309",
        )
        return

    bricklink_url = saved_result.get("BrickLink URL", "")

    if not bricklink_url or bricklink_url == "N/A":
        processing_status_label.configure(
            text="No BrickLink page is available for this result.",
            foreground="#B91C1C",
        )
        return

    webbrowser.open(bricklink_url)    


# FUNCTION GUIDE: _reset_current_batch_workspace
# Purpose: Return the active GUI to a clean, empty batch workspace.
def _reset_current_batch_workspace():
    """Return the active GUI to a clean, empty batch workspace."""

    # This resets visible/in-memory state only. The caller decides whether disk
    # files remain active or were archived before reaching this function.
    selected_image_paths.clear()
    visible_image_paths.clear()
    image_filter_dropdown.set("Needs attention")
    processed_results.clear()
    failed_image_paths.clear()
    pending_rescan_images.clear()
    completed_rescans.clear()
    color_options_by_image.clear()
    color_option_requests.clear()
    previously_saved_paths.clear()
    newly_processed_paths.clear()
    pending_csv_reviews.clear()
    update_all_statuses_button.configure(
        state="disabled",
        text="Update All Statuses (0)",
    )

    image_listbox.delete(0, tk.END)
    uploaded_image_preview.configure(
        image="",
        text="Select one or more LEGO images",
    )
    uploaded_image_preview.image = None
    full_preview_images["uploaded"] = None
    selected_file_label.configure(text="No image selected")
    reference_image_preview.configure(
        image="",
        text="Reference image will appear\nafter processing",
    )
    reference_image_preview.image = None
    full_preview_images["reference"] = None
    part_name_value.configure(text="-")
    part_id_value.configure(text="-")
    lego_color_value.configure(text="-")
    display_confidence_badge(part_confidence_value, "Unknown")
    display_confidence_badge(color_confidence_value, "Unknown")
    display_review_badge(part_review_value, "Unknown")
    display_review_badge(color_review_value, "Unknown")
    color_review_controls.grid_remove()
    rescan_part_button.grid_remove()
    part_save_status_label.grid_remove()
    batch_progress["value"] = 0
    batch_progress["maximum"] = 1
    image_count_label.configure(text="No images selected")
    processing_status_label.configure(
        text="Select an image to begin.",
        foreground="#66727D",
    )
    update_batch_summary()


# FUNCTION GUIDE: clear_current_batch
# Purpose: Clear the current GUI batch without deleting saved files.
def clear_current_batch():
    """
    Clear the current GUI batch without deleting saved files.

    CSV, JSON, review records, and source images remain untouched.
    """

    if process_button.cget("state") == "disabled":
        messagebox.showwarning(
            "Processing in Progress",
            "Please wait until batch processing is complete.",
        )
        return

    if rescan_part_button.cget("text") == "Processing...":
        messagebox.showwarning(
            "Rescan in Progress",
            "Please wait until the part rescan is complete.",
        )
        return

    if not selected_image_paths:
        processing_status_label.configure(
            text="There is no current batch to clear.",
            foreground="#66727D",
        )
        update_batch_summary()
        return

    should_clear = messagebox.askyesno(
        "Clear Current Batch",
        (
            "Clear the currently selected images from the screen?\n\n"
            "Saved CSV and JSON results will not be deleted."
        ),
    )

    if not should_clear:
        return

    if pending_csv_reviews and not update_all_approval_statuses():
        return
    _reset_current_batch_workspace()


def complete_current_batch():
    """Save pending reviews and close the active batch workspace."""

    if process_button.cget("state") == "disabled":
        messagebox.showwarning(
            "Processing in Progress",
            "Please wait until batch processing is complete.",
            parent=window,
        )
        return

    if rescan_part_button.cget("text") == "Processing...":
        messagebox.showwarning(
            "Rescan in Progress",
            "Please wait until the part rescan is complete.",
            parent=window,
        )
        return

    if not selected_image_paths and not pending_csv_reviews:
        messagebox.showinfo(
            "No Active Batch",
            "There is no active batch to complete.",
            parent=window,
        )
        return

    unresolved = sum(needs_attention(processed_results.get(str(Path(path)))) for path in selected_image_paths)
    should_complete = messagebox.askyesno(
        "Complete Batch",
        (
            f"Unresolved pieces: {unresolved}. Only Ready rows count in inventory.\n\n"
            "Save all pending verification changes and finish this review batch?\n\n"
            "The saved CSV and review history will remain available for Step 3. "
            "The Step 2 workspace will be cleared for the next batch."
        ),
        parent=window,
    )
    if not should_complete:
        return

    # A dropdown change is deliberately previewed before it is committed. If
    # the operator finishes the batch while the active row still says
    # "Not saved yet", commit that visible choice before flushing the queue.
    # This prevents Complete Batch from clearing the last color selection.
    color_status = str(color_save_status_label.cget("text"))
    if color_status.startswith("Not saved yet:"):
        confirm_selected_color()
        if str(color_save_status_label.cget("text")).startswith("Not saved yet:"):
            messagebox.showerror(
                "Color Not Saved",
                "The currently selected color could not be saved, so the batch was not completed.",
                parent=window,
            )
            return

    for result in processed_results.values():
        queue_csv_review(result)
    if pending_csv_reviews and not update_all_approval_statuses():
        return

    completed_count = len(processed_results)
    _reset_current_batch_workspace()
    processing_status_label.configure(
        text="Batch completed. Step 2 is ready for a new batch.",
        foreground="#15803D",
    )
    messagebox.showinfo(
        "Batch Completed",
        (
            f"Saved {completed_count} processed result(s).\n\n"
            "Reviewed colors are stored in the Final LEGO Color and Final "
            "BrickLink Color ID columns. The original prediction columns are "
            "kept unchanged for comparison.\n\n"
            "The results are ready for Step 3, and this workspace is now clear."
        ),
        parent=window,
    )


# FUNCTION GUIDE: _choose_new_batch_action
# Purpose: Ask whether to clear the view or archive a completed LEGO set.
def _choose_new_batch_action():
    """Ask whether to clear the view or archive a completed LEGO set."""

    choice = {"value": None}
    choice_window = ctk.CTkToplevel(window)
    choice_window.title("Start New Batch")
    fit_window_to_screen(
        choice_window,
        520,
        335,
        minimum_width=500,
        minimum_height=335,
    )
    choice_window.resizable(False, False)
    choice_window.transient(window)
    choice_window.grab_set()

    ctk.CTkLabel(
        choice_window,
        text="What would you like to do?",
        font=ctk.CTkFont(
            family="Segoe UI",
            size=22,
            weight="bold",
        ),
        text_color=TEXT_PRIMARY,
    ).pack(pady=(24, 8))

    ctk.CTkLabel(
        choice_window,
        text=(
            "Clear only the visible workspace to keep adding to the "
            "same saved files, or finish this LEGO set and archive its "
            "results before starting a separate one."
        ),
        wraplength=450,
        justify="center",
        text_color=TEXT_SECONDARY,
    ).pack(pady=(0, 18))

    # FUNCTION GUIDE: finish_choice
    # Purpose: Record the selected batch action and close the choice window.
    def finish_choice(value):
        """Record the selected batch action and close the choice window."""
        choice["value"] = value
        choice_window.destroy()

    ctk.CTkButton(
        choice_window,
        text="Clear Screen Only",
        command=lambda: finish_choice("clear"),
        width=280,
        height=42,
        fg_color=SECONDARY_BUTTON,
        hover_color=SECONDARY_BUTTON_HOVER,
    ).pack(pady=5)

    ctk.CTkButton(
        choice_window,
        text="Finish & Start New LEGO Set",
        command=lambda: finish_choice("archive"),
        width=280,
        height=42,
        fg_color=PRIMARY_GREEN,
        hover_color=PRIMARY_GREEN_HOVER,
    ).pack(pady=5)

    ctk.CTkButton(
        choice_window,
        text="Cancel",
        command=lambda: finish_choice(None),
        width=120,
        height=34,
        fg_color="#596873",
        hover_color="#45525B",
    ).pack(pady=(9, 18))

    choice_window.protocol(
        "WM_DELETE_WINDOW",
        lambda: finish_choice(None),
    )
    choice_window.wait_window()
    return choice["value"]


# FUNCTION GUIDE: start_new_batch
# Purpose: Clear the view or archive a set and open a clean workspace.
def start_new_batch():
    """Clear the view or archive a set and open a clean workspace."""

    if process_button.cget("state") == "disabled":
        messagebox.showwarning(
            "Processing in Progress",
            "Please wait until batch processing is complete.",
        )
        return

    if rescan_part_button.cget("text") == "Processing...":
        messagebox.showwarning(
            "Rescan in Progress",
            "Please wait until the part rescan is complete.",
        )
        return

    batch_action = _choose_new_batch_action()

    if batch_action is None:
        return
    if pending_csv_reviews and not update_all_approval_statuses():
        return

    # These choices intentionally have different persistence behavior. Clear
    # keeps the dataset; archive creates a hard boundary between LEGO sets.
    if batch_action == "clear":
        _reset_current_batch_workspace()
        processing_status_label.configure(
            text=(
                "Screen cleared. Existing saved files remain active "
                "for this LEGO set."
            ),
            foreground="#15803D",
        )
        return

    batch_name = simpledialog.askstring(
        "Start New Batch",
        (
            "Enter the completed LEGO set or batch name.\n"
            "Example: LEGO Set 75379"
        ),
        parent=window,
    )

    if batch_name is None:
        return

    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", batch_name.strip())
    safe_name = safe_name.strip("_") or "LEGO_Batch"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_folder = BATCH_ARCHIVE_FOLDER / f"{safe_name}_{timestamp}"

    should_start = messagebox.askyesno(
        "Archive and Start New Batch",
        (
            f"Archive the current results as:\n{safe_name}\n\n"
            "The completed CSV, JSON, Excel reports, and captured "
            "photos will be preserved. A clean workspace will then open."
        ),
        parent=window,
    )

    if not should_start:
        return

    archive_candidates = []

    if RESULTS_FOLDER.exists():
        archive_candidates = [
            path
            for path in RESULTS_FOLDER.iterdir()
            if path.name not in {
                BATCH_ARCHIVE_FOLDER.name,
                "part_color_cache.json",
            }
        ]

    moved_paths = []

    # This function runs outside Tkinter's main thread. Only dictionaries and
    # status values cross the queue; check_rescan_queue() owns widget changes.
    try:
        if archive_candidates:
            archive_folder.mkdir(parents=True, exist_ok=False)

            for source_path in archive_candidates:
                destination_path = archive_folder / source_path.name
                shutil.move(
                    str(source_path),
                    str(destination_path),
                )
                moved_paths.append((source_path, destination_path))

        _reset_current_batch_workspace()
        processing_status_label.configure(
            text=f"New batch ready: {batch_name.strip() or safe_name}",
            foreground="#15803D",
        )

        if archive_candidates:
            messagebox.showinfo(
                "New Batch Ready",
                (
                    "The completed batch was archived successfully.\n\n"
                    f"Saved in: {archive_folder}"
                ),
                parent=window,
            )
            show_save_confirmation(
                f"Archived LEGO set {safe_name}"
            )

    except OSError as error:
        # Roll back any completed moves so a partial archive never strands
        # the active batch across two folders.
        for original_path, archived_path in reversed(moved_paths):
            try:
                if archived_path.exists():
                    shutil.move(str(archived_path), str(original_path))
            except OSError:
                pass

        messagebox.showerror(
            "Could Not Start New Batch",
            (
                "The current results could not be archived safely. "
                f"Nothing was cleared.\n\n{error}"
            ),
            parent=window,
        )


def select_review_index(index):
    """Select and display one visible image without relying on mouse timing."""
    if not visible_image_paths:
        return
    index = max(0, min(index, len(visible_image_paths) - 1))
    image_listbox.selection_clear(0, tk.END)
    image_listbox.selection_set(index)
    image_listbox.activate(index)
    image_listbox.see(index)
    show_selected_image()


def move_review_selection(offset):
    selected = image_listbox.curselection()
    current = selected[0] if selected else 0
    select_review_index(current + offset)


def approve_current_and_next():
    """Approve the selected identity, then rebuild the filter before advancing."""
    if process_button.cget("state") == "disabled":
        return
    selected = image_listbox.curselection()
    if not selected or selected[0] >= len(visible_image_paths):
        return
    index = selected[0]
    path = str(Path(visible_image_paths[index]))
    result = processed_results.get(path)
    if not result:
        processing_status_label.configure(text="Process or rescan this piece before approving it.", foreground="#B45309")
        return
    candidate = dict(result)
    if str(color_save_status_label.cget("text")).startswith("Not saved yet:"):
        color = color_review_dropdown.get().strip()
        options = color_options_by_image.get(result["Image"], BRICKLINK_REVIEW_COLORS)
        candidate["LEGO Color"] = color
        candidate["Confirmed LEGO Color"] = color
        candidate["Confirmed BrickLink Color ID"] = options.get(color, "")
    try:
        approved = approve_reviewed_result(candidate)
    except (OSError, ValueError) as error:
        processing_status_label.configure(text=f"Piece was not approved: {error}", foreground="#B91C1C")
        return
    processed_results[path] = approved
    queue_csv_review(approved)
    update_batch_summary()
    apply_image_filter()
    if visible_image_paths:
        # Under Needs attention the old row disappeared; its successor has its index.
        next_index = index + 1 if path in [str(Path(p)) for p in visible_image_paths] else index
        select_review_index(min(next_index, len(visible_image_paths) - 1))


def show_color_choices():
    """Open a searchable, scrollable list of colors for the current part."""

    selected_indices = image_listbox.curselection()
    if not selected_indices:
        return
    image_path = Path(visible_image_paths[selected_indices[0]])
    result = processed_results.get(str(image_path))
    if not result:
        messagebox.showinfo(
            "No Color Result",
            "Identify or rescan this part before changing its color.",
            parent=window,
        )
        return

    image_name = result.get("Image", image_path.name)
    chooser = ctk.CTkToplevel(window)
    chooser.title("Choose LEGO Color")
    fit_window_to_screen(chooser, 480, 560, minimum_width=420, minimum_height=420)
    chooser.transient(window)
    chooser.grab_set()

    ctk.CTkLabel(
        chooser,
        text="Choose LEGO Color",
        font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
        text_color=TEXT_PRIMARY,
    ).pack(pady=(16, 4))
    color_list_status = tk.StringVar(value="Loading available colors…")
    ttk.Label(chooser, textvariable=color_list_status).pack(pady=(0, 8))
    search_value = tk.StringVar()
    search_row = ttk.Frame(chooser)
    search_row.pack(fill="x", padx=16, pady=(0, 8))
    ttk.Label(search_row, text="Search colors:").pack(side="left", padx=(0, 8))
    search_entry = ttk.Entry(search_row, textvariable=search_value)
    search_entry.pack(side="left", fill="x", expand=True)

    list_frame = ttk.Frame(chooser)
    list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 10))
    list_frame.rowconfigure(0, weight=1)
    list_frame.columnconfigure(0, weight=1)
    color_listbox = tk.Listbox(
        list_frame,
        font=("Segoe UI", 11),
        exportselection=False,
        activestyle="none",
    )
    color_scrollbar = ttk.Scrollbar(
        list_frame, orient="vertical", command=color_listbox.yview
    )
    color_listbox.configure(yscrollcommand=color_scrollbar.set)
    color_listbox.grid(row=0, column=0, sticky="nsew")
    color_scrollbar.grid(row=0, column=1, sticky="ns")
    displayed_names = []

    def refresh_color_list(*_args):
        if not chooser.winfo_exists():
            return
        options = dict(color_options_by_image.get(image_name) or {})
        current_color = result.get("LEGO Color", "Unknown")
        current_id = (
            result.get("Confirmed BrickLink Color ID")
            or result.get("BrickLink Color ID")
            or BRICKLINK_REVIEW_COLORS.get(current_color)
        )
        if current_id:
            options[current_color] = current_id
        query = search_value.get().strip().casefold()
        names = [name for name in sorted(options) if query in name.casefold()]
        previous = color_listbox.get(color_listbox.curselection()[0]) if color_listbox.curselection() else current_color
        color_listbox.delete(0, tk.END)
        displayed_names.clear()
        displayed_names.extend(names)
        for name in names:
            color_listbox.insert(tk.END, name)
        if previous in names:
            index = names.index(previous)
            color_listbox.selection_set(index)
            color_listbox.see(index)
        elif names:
            color_listbox.selection_set(0)
        loading = any(key[0] == image_name for key in color_option_requests)
        color_list_status.set(
            f"{len(names)} color(s){' — full list is still loading' if loading else ''}"
        )

    def use_selected_color(_event=None):
        selection = color_listbox.curselection()
        if not selection:
            return
        selected_color = displayed_names[selection[0]]
        options = dict(color_options_by_image.get(image_name) or {})
        color_review_dropdown.configure(values=sorted(options))
        color_review_dropdown.set(selected_color)
        color_review_controls.grid(
            row=3, column=0, columnspan=8, padx=20, pady=(0, 8), sticky="w"
        )
        preview_selected_color(selected_color)
        chooser.destroy()

    def poll_colors():
        if not chooser.winfo_exists():
            return
        refresh_color_list()
        chooser.after(300, poll_colors)

    search_value.trace_add("write", refresh_color_list)
    color_listbox.bind("<Double-Button-1>", use_selected_color)
    button_row = ttk.Frame(chooser)
    button_row.pack(fill="x", padx=16, pady=(0, 16))
    ttk.Button(button_row, text="Use Selected Color", command=use_selected_color).pack(side="right")
    ttk.Button(button_row, text="Cancel", command=chooser.destroy).pack(side="right", padx=(0, 6))
    refresh_color_list()
    search_entry.focus_set()
    chooser.after(300, poll_colors)

    # Keep the compact inline save controls available after choosing a color.
    color_review_controls.grid(
        row=3, column=0, columnspan=8, padx=20, pady=(0, 8), sticky="w"
    )


def choose_similar_part():
    """Show Brickognize's ranked candidates for the selected photograph."""
    selected = image_listbox.curselection()
    if not selected:
        return
    index = selected[0]
    path = str(Path(visible_image_paths[index]))
    result = processed_results.get(path)
    if not result:
        return
    try:
        responses = json.loads(API_RESPONSES_FILE.read_text(encoding="utf-8"))
        detected = responses.get(result.get("Image", ""), {}).get("detected_items", [])
        candidates = detected[0].get("candidate_items", []) if detected else []
    except (OSError, json.JSONDecodeError, TypeError):
        candidates = []
    if not candidates:
        messagebox.showinfo("No alternatives available", "Brickognize did not return alternative candidates for this image.", parent=window)
        return

    chooser = ctk.CTkToplevel(window)
    chooser.title("Choose Similar Part")
    fit_window_to_screen(chooser, 650, 520, minimum_width=520, minimum_height=400)
    chooser.transient(window)
    chooser.grab_set()
    ctk.CTkLabel(chooser, text="Brickognize candidates", font=ctk.CTkFont(size=21, weight="bold")).pack(pady=(18, 4))
    ctk.CTkLabel(chooser, text="Highest confidence is listed first.", text_color=TEXT_SECONDARY).pack(pady=(0, 10))
    body = ctk.CTkScrollableFrame(chooser, fg_color=APP_BACKGROUND)
    body.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def use_candidate(candidate):
        external = next((item for item in candidate.get("external_items", []) if item.get("catalog_name", "").casefold() == "bricklink"), {})
        part_id = str(external.get("external_id") or candidate.get("id", "Unknown")).removeprefix("part-")
        result["Part ID"] = part_id
        result["Part Name"] = external.get("name") or candidate.get("name", "Unknown")
        result["Part Confidence"] = f"{float(candidate.get('score', 0)) * 100:.2f}%"
        result["Part Review Required"] = "No"
        result["Part Review Completed"] = "Yes"
        result["Part Approval Status"] = "Corrected"
        color_id = result.get("Confirmed BrickLink Color ID") or result.get("BrickLink Color ID", "")
        result["BrickLink URL"] = external.get("url") or f"https://www.bricklink.com/v2/catalog/catalogitem.page?P={part_id}"
        if color_id:
            result["BrickLink URL"] = result["BrickLink URL"].split("&idColor=")[0] + f"&idColor={color_id}"
            result["Reference Image URL"] = f"https://img.bricklink.com/ItemImage/PN/{color_id}/{part_id}.png"
        # Color availability is part-specific. Discard the previous part's
        # cached list so the replacement part receives its own valid options.
        color_options_by_image.pop(result.get("Image", ""), None)
        queue_csv_review(result)
        display_reference_image(result)
        display_result_details(result)
        set_image_list_row(index, visible_image_paths[index])
        image_listbox.selection_set(index)
        chooser.destroy()

    for rank, candidate in enumerate(candidates, 1):
        external = next((item for item in candidate.get("external_items", []) if item.get("catalog_name", "").casefold() == "bricklink"), {})
        part_id = str(external.get("external_id") or candidate.get("id", "Unknown")).removeprefix("part-")
        name = external.get("name") or candidate.get("name", "Unknown")
        confidence = float(candidate.get("score", 0)) * 100
        ctk.CTkButton(body, text=f"{rank}. {part_id} — {name}  ({confidence:.1f}%)", command=lambda item=candidate: use_candidate(item), anchor="w", height=42, fg_color=SECONDARY_BUTTON, hover_color=SECONDARY_BUTTON_HOVER).pack(fill="x", pady=4)


def delete_selected_part():
    """Remove the selected recognition record while retaining its source crop."""
    selected = image_listbox.curselection()
    if not selected:
        return
    image_name = Path(visible_image_paths[selected[0]]).name
    if not messagebox.askyesno("Delete identified part?", f"Remove {image_name} from this batch and its saved recognition results?\n\nThe source crop will not be deleted.", parent=window):
        return
    csv_path = RESULTS_FOLDER / "brickognize_results.csv"
    if csv_path.exists() and csv_path.stat().st_size:
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle); fields = reader.fieldnames or []; rows = [row for row in reader if row.get("Image") != image_name]
        temporary = csv_path.with_suffix(".csv.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        temporary.replace(csv_path)
    for json_path in (API_RESPONSES_FILE, RESULTS_FOLDER / "brickognize_reviews.json"):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else {}
            if isinstance(data, dict) and image_name in data:
                data.pop(image_name)
                json_path.write_text(json.dumps(data, indent=4), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass
    pending_csv_reviews.pop(image_name, None)
    remove_selected_image()
    processing_status_label.configure(text=f"Deleted saved identification for {image_name}; source crop retained.", foreground="#15803D")


# FUNCTION GUIDE: remove_selected_image
# Purpose: Remove one image from the active GUI batch.
def remove_selected_image():
    """
    Remove one image from the active GUI batch.

    This changes only the current queue. The source photograph and every
    CSV, JSON, Excel, and review record remain safely on disk.
    """

    if process_button.cget("state") == "disabled":
        processing_status_label.configure(
            text="Wait for processing to finish before removing an image.",
            foreground="#B45309",
        )
        return

    selected_indices = image_listbox.curselection()

    if not selected_indices:
        processing_status_label.configure(
            text="Select an image in the list before using ×.",
            foreground="#B45309",
        )
        return

    selected_index = selected_indices[0]
    image_path = Path(visible_image_paths[selected_index])
    normalized_path = str(image_path)
    image_name = image_path.name

    # Remove only GUI-memory references for this image.
    selected_image_paths[:] = [
        path
        for path in selected_image_paths
        if str(Path(path)) != normalized_path
    ]
    visible_image_paths[:] = [
        path
        for path in visible_image_paths
        if str(Path(path)) != normalized_path
    ]
    processed_results.pop(normalized_path, None)
    pending_rescan_images.pop(normalized_path, None)
    completed_rescans.pop(normalized_path, None)
    color_options_by_image.pop(image_name, None)
    previously_saved_paths.discard(normalized_path)
    newly_processed_paths.discard(normalized_path)

    image_count_label.configure(
        text=(
            f"{len(selected_image_paths)} image(s) selected"
            if selected_image_paths
            else "No images selected"
        ),
    )
    batch_progress["maximum"] = max(len(selected_image_paths), 1)
    batch_progress["value"] = min(
        batch_progress["value"],
        len(selected_image_paths),
    )
    update_batch_summary()

    if selected_image_paths:
        apply_image_filter(image_filter_dropdown.get())

        if not visible_image_paths:
            image_filter_dropdown.set("All Images")
            apply_image_filter("All Images")

        new_index = min(selected_index, image_listbox.size() - 1)
        image_listbox.selection_clear(0, tk.END)
        image_listbox.selection_set(new_index)
        image_listbox.activate(new_index)
        show_selected_image()
    else:
        image_listbox.delete(0, tk.END)
        uploaded_image_preview.configure(
            image="",
            text="Select one or more LEGO images",
        )
        uploaded_image_preview.image = None
        selected_file_label.configure(text="No image selected")
        reference_image_preview.configure(
            image="",
            text="Reference image will appear\nafter processing",
        )
        reference_image_preview.image = None
        full_preview_images["reference"] = None
        part_name_value.configure(text="-")
        part_id_value.configure(text="-")
        lego_color_value.configure(text="-")
        display_confidence_badge(part_confidence_value, "Unknown")
        display_confidence_badge(color_confidence_value, "Unknown")
        display_review_badge(part_review_value, "Unknown")
        display_review_badge(color_review_value, "Unknown")
        color_review_controls.grid_remove()
        rescan_part_button.grid_remove()

    processing_status_label.configure(
        text=(
            f"{image_name} removed from this batch. "
            "The original and saved results were not deleted."
        ),
        foreground="#1D4ED8",
    )


# FUNCTION GUIDE: quit_application
# Purpose: Close the SK BrickRescue application.
def quit_application():
    """Close the SK BrickRescue application."""
    if pending_csv_reviews:
        save_before_exit = messagebox.askyesnocancel(
            "Unsaved approval statuses",
            f"Save {len(pending_csv_reviews)} pending approval status change(s) before closing?",
            parent=window,
        )
        if save_before_exit is None:
            return
        if save_before_exit and not update_all_approval_statuses():
            return
    window.destroy()



# FUNCTION GUIDE: open_results_folder
# Purpose: Open the folder containing the saved CSV and JSON files.


def open_local_path(path):
    """Open an output using the desktop platform's normal application."""
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def open_results_folder():
    """Open the folder containing the saved CSV and JSON files."""

    RESULTS_FOLDER.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        open_local_path(RESULTS_FOLDER)

    except OSError as error:
        print(f"Could not open the results folder: {error}")

        processing_status_label.configure(
            text="The results folder could not be opened.",
            foreground="#B91C1C",
        )


# FUNCTION GUIDE: export_excel_report
# Purpose: Create a new read-only summary workbook from saved result files.
def export_excel_report():
    """
    Create a new read-only summary workbook from saved result files.

    The exporter does not change the CSV, JSON, reviews, or images.
    """

    if not processed_results:
        messagebox.showwarning(
            "No Results to Export",
            "Load or process at least one saved result before exporting.",
        )
        return

    if process_button.cget("state") == "disabled":
        messagebox.showwarning(
            "Processing in Progress",
            "Please wait for the batch to finish before exporting Excel.",
        )
        return

    try:
        report_path = create_excel_report()
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"Excel report error: {error}")
        processing_status_label.configure(
            text="The Excel report could not be created.",
            foreground="#B91C1C",
        )
        return

    processing_status_label.configure(
        text=f"Excel report created: {report_path.name}",
        foreground="#15803D",
    )
    show_save_confirmation(f"Excel report {report_path.name}")

    try:
        open_local_path(report_path)
    except OSError:
        # The report is still successfully saved even if Windows cannot
        # open it automatically.
        pass


# FUNCTION GUIDE: create_branded_help_window
# Purpose: Create a reusable SK BrickRescue information window.
def create_branded_help_window(window_title, heading, subtitle, geometry):
    """Create a reusable SK BrickRescue information window."""

    help_window = ctk.CTkToplevel(window)
    help_window.title(window_title)
    help_window.configure(fg_color=APP_BACKGROUND)
    help_window.transient(window)

    # Open large enough to use immediately, but never beyond the employee's
    # available monitor. The window remains resizable when desired.
    requested_width, requested_height = (
        int(value)
        for value in geometry.lower().split("x", 1)
    )
    fit_window_to_screen(
        help_window,
        requested_width,
        requested_height,
        minimum_width=680,
        minimum_height=560,
    )

    # Closing with Escape feels natural for a secondary desktop window.
    help_window.bind(
        "<Escape>",
        lambda _event: help_window.destroy(),
    )

    header = ctk.CTkFrame(
        help_window,
        fg_color="transparent",
    )
    header.pack(
        fill="x",
        padx=28,
        pady=(22, 14),
    )

    if LOGO_FILE.exists():
        with Image.open(LOGO_FILE) as logo_source:
            help_logo_source = logo_source.copy()

        help_logo = ctk.CTkImage(
            light_image=help_logo_source,
            dark_image=help_logo_source,
            size=(72, 72),
        )
        logo_label = ctk.CTkLabel(
            header,
            text="",
            image=help_logo,
        )
        logo_label.image = help_logo
        logo_label.pack(
            side="left",
            padx=(0, 14),
        )

    title_area = ctk.CTkFrame(
        header,
        fg_color="transparent",
    )
    title_area.pack(
        side="left",
        fill="x",
        expand=True,
    )

    ctk.CTkLabel(
        title_area,
        text=heading,
        font=ctk.CTkFont(
            family="Segoe UI",
            size=23,
            weight="bold",
        ),
        text_color=TEXT_PRIMARY,
        anchor="w",
    ).pack(
        fill="x",
    )

    ctk.CTkLabel(
        title_area,
        text=subtitle,
        font=ctk.CTkFont(
            family="Segoe UI",
            size=12,
        ),
        text_color=TEXT_SECONDARY,
        anchor="w",
    ).pack(
        fill="x",
        pady=(3, 0),
    )

    content = ctk.CTkScrollableFrame(
        help_window,
        fg_color=CARD_BACKGROUND,
        corner_radius=14,
        border_width=1,
        border_color="#D9DEE3",
    )
    content.pack(
        fill="both",
        expand=True,
        padx=28,
        pady=(0, 14),
    )

    close_button = ctk.CTkButton(
        help_window,
        text="Close",
        command=help_window.destroy,
        width=120,
        height=36,
        corner_radius=8,
        fg_color=SECONDARY_BUTTON,
        hover_color=SECONDARY_BUTTON_HOVER,
        font=ctk.CTkFont(
            family="Segoe UI",
            size=12,
            weight="bold",
        ),
    )
    close_button.pack(pady=(0, 18))

    help_window.after(
        100,
        help_window.focus_force,
    )

    return content


# FUNCTION GUIDE: add_workflow_step
# Purpose: Add one numbered instruction card to the workflow guide.
def add_workflow_step(parent, number, title, description):
    """Add one numbered instruction card to the workflow guide."""

    step_card = ctk.CTkFrame(
        parent,
        fg_color="#F8FAFC",
        corner_radius=10,
        border_width=1,
        border_color="#E2E8F0",
    )
    step_card.pack(
        fill="x",
        padx=14,
        pady=6,
    )

    number_badge = ctk.CTkLabel(
        step_card,
        text=str(number),
        width=38,
        height=38,
        corner_radius=19,
        fg_color=PRIMARY_GREEN,
        text_color="#FFFFFF",
        font=ctk.CTkFont(
            family="Segoe UI",
            size=14,
            weight="bold",
        ),
    )
    number_badge.pack(
        side="left",
        padx=14,
        pady=12,
    )

    step_text = ctk.CTkFrame(
        step_card,
        fg_color="transparent",
    )
    step_text.pack(
        side="left",
        fill="x",
        expand=True,
        padx=(0, 14),
        pady=10,
    )

    ctk.CTkLabel(
        step_text,
        text=title,
        font=ctk.CTkFont(
            family="Segoe UI",
            size=13,
            weight="bold",
        ),
        text_color=TEXT_PRIMARY,
        anchor="w",
    ).pack(fill="x")

    ctk.CTkLabel(
        step_text,
        text=description,
        font=ctk.CTkFont(
            family="Segoe UI",
            size=11,
        ),
        text_color=TEXT_SECONDARY,
        justify="left",
        anchor="w",
        wraplength=700,
    ).pack(
        fill="x",
        pady=(2, 0),
    )


# FUNCTION GUIDE: add_workflow_legend
# Purpose: Add a compact visual key for list and save-status symbols.
def add_workflow_legend(parent):
    """Add a compact visual key for list and save-status symbols."""

    legend_card = ctk.CTkFrame(
        parent,
        fg_color="#EFF6FF",
        corner_radius=10,
        border_width=1,
        border_color="#BFDBFE",
    )
    legend_card.pack(fill="x", padx=14, pady=(14, 8))

    ctk.CTkLabel(
        legend_card,
        text="Visual Status Key",
        font=ctk.CTkFont(
            family="Segoe UI",
            size=14,
            weight="bold",
        ),
        text_color=TEXT_PRIMARY,
    ).pack(pady=(10, 6))

    legend_row = ctk.CTkFrame(
        legend_card,
        fg_color="transparent",
    )
    legend_row.pack(padx=10, pady=(0, 10))

    legend_items = [
        ("✓", "Ready / Saved", "#DCFCE7", "#166534"),
        ("⚠", "Check Suggested", "#FEF3C7", "#92400E"),
        ("↻", "Saved Result Reused", "#DBEAFE", "#1D4ED8"),
        ("…", "Processing", "#E0F2FE", "#0369A1"),
        ("✕", "Processing Failed", "#FEE2E2", "#991B1B"),
    ]

    for symbol, meaning, background, foreground in legend_items:
        item = ctk.CTkFrame(
            legend_row,
            fg_color=background,
            corner_radius=8,
        )
        item.pack(side="left", padx=4)
        ctk.CTkLabel(
            item,
            text=f"{symbol}  {meaning}",
            font=ctk.CTkFont(
                family="Segoe UI",
                size=10,
                weight="bold",
            ),
            text_color=foreground,
        ).pack(padx=9, pady=6)


# FUNCTION GUIDE: show_workflow_guide
# Purpose: Open the branded employee workflow guide.
def show_workflow_guide():
    """Open the branded employee workflow guide."""

    content = create_branded_help_window(
        "Workflow Guide",
        "How to Use SK BrickRescue",
        "Every available path from image selection through final reporting",
        "900x720",
    )

    quick_path = ctk.CTkFrame(
        content,
        fg_color="#ECFDF3",
        corner_radius=10,
        border_width=1,
        border_color="#BBF7D0",
    )
    quick_path.pack(fill="x", padx=14, pady=(14, 6))
    ctk.CTkLabel(
        quick_path,
        text=(
            "QUICK PATH  →  Select Images  →  Process  →  Inspect  →  "
            "Adjust if needed  →  Export Excel"
        ),
        font=ctk.CTkFont(
            family="Segoe UI",
            size=12,
            weight="bold",
        ),
        text_color="#166534",
    ).pack(padx=14, pady=10)

    add_workflow_legend(content)

    workflow_steps = [
        (
            "Select Images - Two Ways",
            "Use the large Select LEGO Images button or File > Select LEGO "
            "Images. Choose one JPG/PNG or a full batch. Original files are "
            "displayed in place and are never moved or deleted.",
        ),
        (
            "Review the Queue Before Processing",
            "Use × to remove only the highlighted image. Use the filter to "
            "show All Images, Suggested Checks, Part Check, Color Check, or "
            "Ready. A blue ↻ means that filename already has a reusable "
            "saved CSV result.",
        ),
        (
            "Process - Button or Batch Menu",
            "Select Process All Images. Brickognize identifies each part and "
            "color for new files while the progress bar, row symbols, and "
            "counters update. The same action is under Batch > Process All "
            "Images. Blue ↻ rows are reused rather than processed again.",
        ),
        (
            "Confirm That Results Saved",
            "Every successful result saves CSV and JSON automatically. Watch "
            "the timestamped Last saved message in the toolbar. If a row "
            "shows ✕, it failed and was not reported as saved.",
        ),
        (
            "Inspect the Complete LEGO Images",
            "Select any filename to compare the uploaded photo with its "
            "full-size BrickLink catalog reference. Review Part ID, name, "
            "color, confidence, and suggested-check badges. Open BrickLink "
            "for the live catalog page when needed.",
        ),
        (
            "Verify or Change Color - Always Available",
            "The color selector is available for every processed result, "
            "including Ready rows. Select a supported color to preview its "
            "catalog image. Not saved yet appears after a change. Select Save "
            "Color and confirm ✓ Saved plus the toolbar Last saved message.",
        ),
        (
            "Rescan Part - Camera or Upload",
            "Select Rescan Part for any processed result. Choose Take Photo "
            "Now > Capture Photo > Use This Photo, with Retake available; "
            "camera photos save as the next active-batch lego_XX.jpg. Or use "
            "Upload Existing Photo. Compare both results, then Keep Original "
            "Result or Use Rescan Result. Confirm the saved Part ID.",
        ),
        (
            "Clear the Screen or Finish the LEGO Set",
            "Select Start New Batch. Clear Screen Only removes the visible "
            "queue but keeps the same active saved files. Finish & Start New "
            "LEGO Set asks for a name, archives CSV/JSON/Excel/camera files, "
            "and creates a clean independent workspace. Cancel changes nothing.",
        ),
        (
            "Export Excel - Two Ways",
            "Use the green spreadsheet icon beside Open Results Folder or "
            "Reports > Export Excel Report. Both create and open the same "
            "formatted workbook with "
            "Dashboard, Batch Results, Manual Reviews, API Audit, and "
            "Instructions & Key sheets. Export again after later corrections "
            "to create a fresh snapshot.",
        ),
        (
            "Open Saved Files",
            "Select Open Results Folder or File > Open Results Folder for "
            "direct access. Current files live in results; completed LEGO "
            "sets live in results/completed_batches; accepted camera images "
            "live in results/captured_images until the set is archived.",
        ),
        (
            "If Camera, Brickognize, or BrickLink Is Unavailable",
            "Close other camera apps and confirm Windows camera permission. "
            "Use Upload Existing Photo if camera capture is unavailable. Do "
            "not repeatedly process during a website outage; use saved ↻ "
            "results, reviews, and reports until service returns.",
        ),
    ]

    for step_number, (step_title, description) in enumerate(
        workflow_steps,
        start=1,
    ):
        add_workflow_step(
            content,
            step_number,
            step_title,
            description,
        )

    note = ctk.CTkLabel(
        content,
        text=(
            "Safety note: removing a row or clearing only the screen never "
            "deletes source photographs or saved result files."
        ),
        font=ctk.CTkFont(
            family="Segoe UI",
            size=11,
            slant="italic",
        ),
        text_color=TEXT_SECONDARY,
        justify="center",
        wraplength=620,
    )
    note.pack(
        fill="x",
        padx=20,
        pady=(14, 18),
    )


# FUNCTION GUIDE: show_about_dialog
# Purpose: Open the branded business and application information window.
def show_about_dialog():
    """Open the branded business and application information window."""

    content = create_branded_help_window(
        "About SK BrickRescue Part Recognition",
        "SK BrickRescue Part Recognition",
        "Application purpose, workflow, development, and company context",
        "760x620",
    )

    mission_card = ctk.CTkFrame(
        content,
        fg_color="#ECFDF3",
        corner_radius=12,
        border_width=1,
        border_color="#BBF7D0",
    )
    mission_card.pack(
        fill="x",
        padx=16,
        pady=(16, 8),
    )

    ctk.CTkLabel(
        mission_card,
        text="Giving old bricks new life",
        font=ctk.CTkFont(
            family="Segoe UI",
            size=17,
            weight="bold",
        ),
        text_color=CLEAR_BADGE_TEXT,
    ).pack(pady=(16, 5))

    ctk.CTkLabel(
        mission_card,
        text=(
            "SK BrickRescue supports the reuse and resale of LEGO pieces. "
            "Accurate identification helps turn unsorted parts into useful "
            "inventory that can be enjoyed again."
        ),
        font=ctk.CTkFont(
            family="Segoe UI",
            size=12,
        ),
        text_color=TEXT_PRIMARY,
        justify="center",
        wraplength=570,
    ).pack(
        padx=24,
        pady=(0, 16),
    )

    sections = [
        (
            "What this application does",
            "Processes individual images or large batches, identifies LEGO "
            "parts and colors, displays BrickLink references, and flags "
            "uncertain predictions for employee review.",
        ),
        (
            "Human review stays in control",
            "The application provides confidence indicators, part-specific "
            "color verification for every result, alternate-angle comparison, "
            "and saved employee "
            "decisions instead of silently replacing uncertain results.",
        ),
        (
            "Results built for operations",
            "Successful results are stored as CSV and JSON, while the Excel "
            "export summarizes batch readiness, review activity, final "
            "decisions, and API traceability.",
        ),
        (
            "Safe queue controls",
            "Employees may remove one selected image or clear the active "
            "batch without deleting original photographs or saved records.",
        ),
    ]

    for section_title, section_text in sections:
        section_card = ctk.CTkFrame(
            content,
            fg_color="#F8FAFC",
            corner_radius=10,
            border_width=1,
            border_color="#E2E8F0",
        )
        section_card.pack(
            fill="x",
            padx=16,
            pady=7,
        )

        ctk.CTkLabel(
            section_card,
            text=section_title,
            font=ctk.CTkFont(
                family="Segoe UI",
                size=13,
                weight="bold",
            ),
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(
            fill="x",
            padx=16,
            pady=(13, 3),
        )

        ctk.CTkLabel(
            section_card,
            text=section_text,
            font=ctk.CTkFont(
                family="Segoe UI",
                size=11,
            ),
            text_color=TEXT_SECONDARY,
            justify="left",
            anchor="w",
            wraplength=590,
        ).pack(
            fill="x",
            padx=16,
            pady=(0, 13),
        )

    ctk.CTkLabel(
        content,
        text="SK BrickRescue | Part Recognition",
        font=ctk.CTkFont(
            family="Segoe UI",
            size=10,
        ),
        text_color=TEXT_SECONDARY,
    ).pack(pady=(12, 18))


# FUNCTION GUIDE: apply_saved_part_review
# Purpose: Apply a previously completed part-review decision.
def apply_saved_part_review(result):
    """
    Apply a previously completed part-review decision.

    Returns True when a saved part review was found.
    """

    image_name = result.get("Image", "")
    saved_review = load_part_review(image_name)

    if not saved_review:
        return False

    decision = saved_review.get("Decision", "")

    if decision == "Use Rescan Result":
        final_part_id = saved_review.get(
            "Final Part ID",
            result.get("Part ID", "Unknown"),
        )

        result["Part ID"] = final_part_id
        result["Part Name"] = saved_review.get(
            "Final Part Name",
            result.get("Part Name", "Unknown"),
        )
        result["Part Confidence"] = saved_review.get(
            "Final Part Confidence",
            result.get("Part Confidence", "Unknown"),
        )

        rescan_color = saved_review.get("Rescan LEGO Color")
        rescan_color_id = saved_review.get("Rescan BrickLink Color ID")
        if rescan_color:
            result["LEGO Color"] = rescan_color
            result["Predicted LEGO Color"] = rescan_color
            result["Color Confidence"] = saved_review.get(
                "Rescan Color Confidence", result.get("Color Confidence", "Unknown")
            )
        if rescan_color_id:
            result["BrickLink Color ID"] = rescan_color_id
            result["Predicted BrickLink Color ID"] = rescan_color_id

        # Preserve the result's current color when rebuilding its links.
        current_bricklink_url = result.get(
            "BrickLink URL",
            "",
        )

        current_color_id = str(rescan_color_id or "")

        if not current_color_id and "&idColor=" in current_bricklink_url:
            current_color_id = current_bricklink_url.split(
                "&idColor=",
                1,
            )[1].split("&", 1)[0]

        result["BrickLink URL"] = (
            "https://www.bricklink.com/v2/catalog/"
            f"catalogitem.page?P={final_part_id}"
        )

        if current_color_id:
            result["BrickLink URL"] += (
                f"&idColor={current_color_id}"
            )

            result["Reference Image URL"] = (
                "https://img.bricklink.com/ItemImage/PN/"
                f"{current_color_id}/{final_part_id}.png"
            )

    result["Part Review Required"] = "No"
    result["Part Review Completed"] = "Yes"
    result["Part Review Decision"] = decision
    result["Part Approval Status"] = (
        "Corrected" if decision == "Use Rescan Result" else "Confirmed"
    )

    return True


# FUNCTION GUIDE: apply_saved_color_review
# Purpose: Apply a previously confirmed color to a processed result.
def apply_saved_color_review(result):
    """
    Apply a previously confirmed color to a processed result.

    Returns True when a saved review was found.
    """

    image_name = result.get("Image", "")
    saved_review = load_color_review(image_name)

    if not saved_review:
        return False

    confirmed_color = saved_review.get(
        "Confirmed Color",
        result.get("LEGO Color", "Unknown"),
    )

    confirmed_color_id = saved_review.get(
        "Confirmed BrickLink Color ID",
        "",
    )

    result["Original LEGO Color"] = saved_review.get(
        "Original Color",
        result.get("LEGO Color", "Unknown"),
    )
    result["Confirmed LEGO Color"] = confirmed_color
    result["Confirmed BrickLink Color ID"] = confirmed_color_id
    result["Color Review Completed"] = "Yes"
    result["Color Approval Status"] = saved_review.get(
        "Color Approval Status",
        (
            "Confirmed"
            if confirmed_color == result["Original LEGO Color"]
            else "Corrected"
        ),
    )
    result["LEGO Color"] = confirmed_color
    result["Color Review Required"] = "No"

    part_id = result.get("Part ID", "")

    if confirmed_color_id and part_id:
        result["Reference Image URL"] = (
            "https://img.bricklink.com/ItemImage/PN/"
            f"{confirmed_color_id}/{part_id}.png"
        )

        bricklink_url = result.get("BrickLink URL", "")

        if bricklink_url and bricklink_url != "N/A":
            base_url = bricklink_url.split("&idColor=")[0]

            result["BrickLink URL"] = (
                f"{base_url}&idColor={confirmed_color_id}"
            )

    return True


# FUNCTION GUIDE: show_selected_image
# Purpose: Display the image currently selected in the filename list.
def reviewed_source_image(image_path):
    """Return the accepted rescan photo when a saved review chose it."""

    original_path = Path(image_path)
    saved_review = load_part_review(original_path.name)
    if not saved_review or saved_review.get("Decision") != "Use Rescan Result":
        return original_path
    candidates = []
    saved_path = saved_review.get("Rescan Image Path", "")
    if saved_path:
        candidates.append(Path(saved_path))
    saved_name = saved_review.get("Rescan Image", "")
    if saved_name:
        candidates.extend(
            (CAPTURED_IMAGES_FOLDER / saved_name, original_path.parent / saved_name)
        )
    return next((path for path in candidates if path.is_file()), original_path)


def show_selected_image(event=None):
    """Display the image currently selected in the filename list."""

    selected_indices = image_listbox.curselection()

    if not selected_indices:
        return

    selected_index = selected_indices[0]
    image_path = visible_image_paths[selected_index]

    display_uploaded_image(reviewed_source_image(image_path))

    saved_result = processed_results.get(
        str(Path(image_path))
    )

    if saved_result:
        processing_status_label.configure(
            text=f"{Path(image_path).name} has a saved result.",
            foreground="#15803D",
        )

        part_review_loaded = apply_saved_part_review(
            saved_result
        )
        color_review_loaded = apply_saved_color_review(
            saved_result
        )

        if part_review_loaded or color_review_loaded:
            image_name = Path(image_path).name

            set_image_list_row(
                selected_index,
                image_path,
            )
            image_listbox.selection_set(selected_index)
            image_listbox.activate(selected_index)    

        display_reference_image(saved_result)
        display_result_details(saved_result)
        

    else:
        processing_status_label.configure(
            text=(
                f"{Path(image_path).name} has no identification result. "
                "Use Rescan to try another photo or angle."
            ),
            foreground="#B45309",
        )

        reference_image_preview.configure(
            image="",
            text="No match found\nRescan from another angle",
        )
        reference_image_preview.image = None
        full_preview_images["reference"] = None
        part_name_value.configure(text="No match")
        part_id_value.configure(text="-")
        lego_color_value.configure(text="-")
        display_confidence_badge(part_confidence_value, "Unknown")
        display_confidence_badge(color_confidence_value, "Unknown")
        display_review_badge(part_review_value, "Yes")
        display_review_badge(color_review_value, "Unknown")
        rescan_part_button.configure(
            text="Rescan Part",
            command=select_rescan_image,
            state="normal",
            fg_color=SECONDARY_BUTTON,
            hover_color=SECONDARY_BUTTON_HOVER,
        )
        rescan_part_button.grid(
            row=2,
            column=0,
            columnspan=2,
            padx=(20, 8),
            pady=(0, 8),
            sticky="w",
        )
        part_save_status_label.grid_remove()
        color_review_controls.grid_remove()

        reference_image_preview.configure(
            image="",
            text="Reference image will appear\nafter processing",
        )
        reference_image_preview.image = None

        part_name_value.configure(text="-")
        part_id_value.configure(text="-")
        lego_color_value.configure(text="-")
        display_confidence_badge(
            part_confidence_value,
            "Unknown",
        )
        display_confidence_badge(
            color_confidence_value,
            "Unknown",
        )
        display_review_badge(
            part_review_value,
            "Unknown",
        )
        display_review_badge(
            color_review_value,
            "Unknown",
        )


# FUNCTION GUIDE: apply_image_filter
# Purpose: Display matching images while preserving the active selection.
def apply_image_filter(selected_filter=None):
    """Display matching images while preserving the active selection."""

    if selected_filter is None:
        selected_filter = image_filter_dropdown.get()

    # Rebuilding a Tkinter listbox clears its selection. Remember the
    # selected image by path before deleting the rows so saves, reviews,
    # and rescans do not unexpectedly jump the employee back to row one.
    selected_path = None
    selected_indices = image_listbox.curselection()

    if selected_indices:
        selected_index = selected_indices[0]

        if selected_index < len(visible_image_paths):
            selected_path = str(
                Path(visible_image_paths[selected_index])
            )

    visible_image_paths.clear()
    image_listbox.delete(0, tk.END)

    for image_path in selected_image_paths:
        normalized_path = str(Path(image_path))
        result = processed_results.get(normalized_path)

        needs_review = needs_attention(result)
        part_review_required = bool(result) and result.get("Part Review Required") == "Yes"
        color_review_required = bool(result) and result.get("Color Review Required") == "Yes"
        is_processed = bool(result)

        should_display = (
            selected_filter == "All Images"
            or (selected_filter == "Needs attention" and needs_review)
            or (
                selected_filter == "Suggested Checks"
                and needs_review
            )
            or (
                selected_filter == "Part Check"
                and part_review_required
            )
            or (
                selected_filter == "Color Check"
                and color_review_required
            )
            or (
                selected_filter == "Ready"
                and is_processed
                and not needs_review
            )
        )

        if not should_display:
            continue

        visible_image_paths.append(image_path)

    if process_button.cget("state") != "disabled":
        visible_image_paths.sort(key=lambda path: review_group(processed_results.get(str(Path(path)))))
    for image_path in visible_image_paths:
        set_image_list_row(image_listbox.size(), image_path)

    if visible_image_paths:
        restored_index = 0

        if selected_path is not None:
            for index, image_path in enumerate(visible_image_paths):
                if str(Path(image_path)) == selected_path:
                    restored_index = index
                    break

        image_listbox.selection_set(restored_index)
        image_listbox.activate(restored_index)
        image_listbox.see(restored_index)
        show_selected_image()
    else:
        color_save_status_label.configure(text="No piece selected")
        processing_status_label.configure(
            text=f"No images match: {selected_filter}.",
            foreground="#66727D",
        )



# FUNCTION GUIDE: update_batch_summary
# Purpose: Update the counters for the currently selected batch.
def update_batch_summary():
    """Update the counters for the currently selected batch."""

    selected_count = len(selected_image_paths)
    newly_processed_count = sum(
        1
        for image_path in selected_image_paths
        if str(Path(image_path)) in newly_processed_paths
    )
    ready_count = 0
    part_review_count = 0
    color_review_count = 0

    for image_path in selected_image_paths:
        result = processed_results.get(
            str(Path(image_path))
        )

        if not result:
            continue

        apply_review_policy(result)
        part_review_required = (
            result.get("Part Review Required") == "Yes"
        )
        color_review_required = (
            result.get("Color Review Required") == "Yes"
        )

        if part_review_required:
            part_review_count += 1

        if color_review_required:
            color_review_count += 1

        if (
            not part_review_required
            and not color_review_required
        ):
            ready_count += 1

    selected_summary_label.configure(
        text=f"Selected\n{selected_count}",
    )
    processed_summary_label.configure(
        text=f"Newly Processed\n{newly_processed_count}",
    )
    ready_summary_label.configure(
        text=f"Ready: {ready_count}\nUnresolved: {selected_count - ready_count}",
    )
    part_review_summary_label.configure(
        text=f"Part Check\n{part_review_count}",
    )
    color_review_summary_label.configure(
        text=f"Color Check\n{color_review_count}",
    )
    update_excel_export_state()

# FUNCTION GUIDE: upload_images
# Purpose: Allow the user to select one or multiple LEGO images.
def upload_images(image_paths=None):
    """Add one or multiple LEGO images to the active batch."""

    if image_paths is None:
        image_paths = filedialog.askopenfilenames(
            title="Select LEGO Images",
            initialdir=DEFAULT_IMAGE_FOLDER,
            filetypes=[
                ("Image Files", "*.jpg *.jpeg *.png"),
                ("JPEG Files", "*.jpg *.jpeg"),
                ("PNG Files", "*.png"),
            ],
        )

    if not image_paths:
        return

    # Repeated selections add another segmentation folder to the same batch.
    # The CSV uses the basename as its persistent key, so reject both an exact
    # path duplicate and a different file carrying an already-selected name.
    selected_normalized = {
        str(Path(path).resolve()).casefold() for path in selected_image_paths
    }
    selected_names = {Path(path).name.casefold() for path in selected_image_paths}
    added_paths = []
    skipped_names = []
    for image_path in image_paths:
        normalized = str(Path(image_path).resolve()).casefold()
        image_name = Path(image_path).name.casefold()
        if normalized in selected_normalized or image_name in selected_names:
            skipped_names.append(Path(image_path).name)
            continue
        selected_image_paths.append(image_path)
        added_paths.append(image_path)
        selected_normalized.add(normalized)
        selected_names.add(image_name)

    if not added_paths:
        messagebox.showinfo(
            "No new images added",
            "Every selected image is already in this batch, or has a filename already in use.",
            parent=window,
        )
        return

    visible_image_paths.clear()
    visible_image_paths.extend(selected_image_paths)
    image_filter_dropdown.set("All Images")

    # Restore matching CSV rows before drawing the list. These images can be
    # reviewed immediately and will not be sent to Brickognize a second time.
    recorded_results = get_recorded_csv_results()

    for image_path in added_paths:
        normalized_path = str(Path(image_path))
        saved_result = recorded_results.get(Path(image_path).name)

        if saved_result:
            apply_saved_part_review(saved_result)
            apply_saved_color_review(saved_result)
            processed_results[normalized_path] = saved_result
            queue_csv_review(saved_result)
            previously_saved_paths.add(normalized_path)

    # Refresh the filename list.
    image_listbox.delete(0, tk.END)

    for image_path in selected_image_paths:
        set_image_list_row(
            image_listbox.size(),
            image_path,
        )

    image_count_label.configure(
        text=f"{len(selected_image_paths)} image(s) selected",
    )

    repeated_filenames = [
        Path(image_path).name
        for image_path in added_paths
        if str(Path(image_path)) in previously_saved_paths
    ]

    update_batch_summary()

    # Reset the progress bar for the newly selected batch.
    batch_progress["maximum"] = len(selected_image_paths)
    batch_progress["value"] = 0
    window.update_idletasks()

    # Select the first image added by this picker operation.
    first_added_index = selected_image_paths.index(added_paths[0])
    image_listbox.selection_set(first_added_index)
    image_listbox.activate(first_added_index)
    image_listbox.see(first_added_index)
    show_selected_image()

    image_filter_dropdown.set("Needs attention")
    apply_image_filter()

    # show_selected_image() normally reports the selected row's state.
    # Replace that message here so an existing CSV record cannot be missed.
    if repeated_filenames:
        displayed_names = ", ".join(repeated_filenames[:5])
        if len(repeated_filenames) > 5:
            displayed_names += ", …"

        processing_status_label.configure(
            text=(
                "Blue ↻ rows already have saved CSV results: "
                f"{displayed_names}"
            ),
            foreground="#1D4ED8",
        )
        messagebox.showinfo(
            "Previous Results Loaded",
            (
                f"Loaded saved results for {len(repeated_filenames)} image(s).\n\n"
                "They are marked with a blue ↻ in the list. You can continue "
                "reviewing them, or use Complete Batch when this job is finished."
            ),
            parent=window,
        )
    else:
        processing_status_label.configure(
            text=(
                f"Added {len(added_paths)} image(s) to this batch. "
                "Select another folder or click Process All Images."
            ),
            foreground="#1D4ED8",
        )

    if skipped_names:
        displayed_names = ", ".join(skipped_names[:5])
        if len(skipped_names) > 5:
            displayed_names += ", …"
        messagebox.showwarning(
            "Some images were not added",
            "These filenames were already present in the batch and were skipped:\n\n"
            f"{displayed_names}",
            parent=window,
        )










def upload_image_folders():
    """Collect several folders and add every image found inside them."""

    if process_button.cget("state") == "disabled":
        messagebox.showwarning(
            "Processing in Progress",
            "Please wait until the current processing run is complete.",
            parent=window,
        )
        return

    folder_window = ctk.CTkToplevel(window)
    folder_window.title("Add Image Folders")
    fit_window_to_screen(
        folder_window, 620, 430, minimum_width=520, minimum_height=360
    )
    folder_window.transient(window)
    folder_window.grab_set()

    ctk.CTkLabel(
        folder_window,
        text="Choose image folders",
        font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
        text_color=TEXT_PRIMARY,
    ).pack(pady=(18, 4))
    ctk.CTkLabel(
        folder_window,
        text=(
            "Add as many folders as needed. JPG, JPEG, and PNG files in "
            "those folders and their subfolders will be loaded."
        ),
        wraplength=560,
        text_color=TEXT_SECONDARY,
    ).pack(pady=(0, 12))

    list_frame = ttk.Frame(folder_window)
    list_frame.pack(fill="both", expand=True, padx=18, pady=(0, 10))
    list_frame.rowconfigure(0, weight=1)
    list_frame.columnconfigure(0, weight=1)
    folder_list = tk.Listbox(list_frame, font=("Segoe UI", 10), exportselection=False)
    folder_scroll = ttk.Scrollbar(list_frame, command=folder_list.yview)
    folder_list.configure(yscrollcommand=folder_scroll.set)
    folder_list.grid(row=0, column=0, sticky="nsew")
    folder_scroll.grid(row=0, column=1, sticky="ns")
    chosen_folders = []

    def add_folder():
        initial_folder = chosen_folders[-1] if chosen_folders else DEFAULT_IMAGE_FOLDER
        selected_folder = filedialog.askdirectory(
            parent=folder_window,
            title="Choose a Folder Containing LEGO Images",
            initialdir=initial_folder,
            mustexist=True,
        )
        if not selected_folder:
            return
        normalized = str(Path(selected_folder).resolve())
        if normalized.casefold() in {path.casefold() for path in chosen_folders}:
            return
        chosen_folders.append(normalized)
        folder_list.insert(tk.END, normalized)

    def remove_folder():
        selection = folder_list.curselection()
        if not selection:
            return
        index = selection[0]
        folder_list.delete(index)
        chosen_folders.pop(index)

    def load_folders():
        if not chosen_folders:
            messagebox.showwarning(
                "No Folders Selected",
                "Add at least one image folder first.",
                parent=folder_window,
            )
            return
        supported_extensions = {".jpg", ".jpeg", ".png"}
        found_images = sorted(
            {
                str(path.resolve())
                for folder in chosen_folders
                for path in Path(folder).rglob("*")
                if path.is_file() and path.suffix.casefold() in supported_extensions
            },
            key=str.casefold,
        )
        if not found_images:
            messagebox.showinfo(
                "No Images Found",
                "No JPG, JPEG, or PNG images were found in the selected folders.",
                parent=folder_window,
            )
            return
        folder_window.destroy()
        upload_images(found_images)

    button_row = ttk.Frame(folder_window)
    button_row.pack(fill="x", padx=18, pady=(0, 18))
    ttk.Button(button_row, text="Add Folder", command=add_folder).pack(side="left", padx=(0, 5))
    ttk.Button(button_row, text="Remove Selected", command=remove_folder).pack(side="left", padx=5)
    ttk.Button(button_row, text="Load All Images", command=load_folders).pack(side="right", padx=(5, 0))
    ttk.Button(button_row, text="Cancel", command=folder_window.destroy).pack(side="right", padx=5)


# FUNCTION GUIDE: start_batch_processing
# Purpose: Prepare the interface and start background processing.
def start_batch_processing():
    """Prepare the interface and start background processing."""

    if process_button.cget("state") == "disabled":
        return
    duplicate_part_warnings.clear()

    if not selected_image_paths:
        processing_status_label.configure(
            text="Please select one or more images first.",
            foreground="#B45309",
        )
        return

    process_button.configure(state="disabled")
    # Processing updates use the full list's row numbers.
    image_filter_dropdown.set("All Images")
    apply_image_filter("All Images")
    image_filter_dropdown.configure(state="disabled")

    # Prevent the selected batch from changing during processing.
    upload_button.configure(state="disabled")
    folder_upload_button.configure(state="disabled")
    process_button.configure(state="disabled")
    set_excel_export_enabled(False)

    # Keep completed rows visible and send only unprocessed images to
    # Brickognize. This also allows a saved batch to be reopened safely.
    pending_items = [
        (row_index, image_path)
        for row_index, image_path in enumerate(selected_image_paths)
        if str(Path(image_path)) not in processed_results
    ]

    if not pending_items:
        image_filter_dropdown.configure(state="readonly")
        upload_button.configure(state="normal")
        folder_upload_button.configure(state="normal")
        process_button.configure(state="normal")
        update_excel_export_state()
        image_filter_dropdown.set("Needs attention")
        apply_image_filter()
        processing_status_label.configure(
            text="Every image in the current batch is already processed.",
            foreground="#15803D",
        )
        return

    total_images = len(selected_image_paths)
    completed_before_start = total_images - len(pending_items)

    batch_progress["maximum"] = total_images
    batch_progress["value"] = completed_before_start

    processing_status_label.configure(
        text=(
            f"Preparing to process {len(pending_items)} new image(s)..."
        ),
        foreground="#1D4ED8",
    )

    worker = threading.Thread(
        target=run_batch_processing,
        args=(pending_items, completed_before_start),
        daemon=True,
    )
    worker.start()

    # Begin checking for messages from the worker.
    window.after(100, check_processing_queue)


# FUNCTION GUIDE: run_batch_processing
# Purpose: Process images in the background without controlling widgets.
def run_batch_processing(pending_items, completed_before_start=0):
    """Process images in the background without controlling widgets."""

    # Never configure a widget in this loop. Messages placed on processing_queue
    # are consumed by check_processing_queue() on Tkinter's main event loop.
    total_pending = len(pending_items)

    for pending_index, item in enumerate(pending_items):
        row_index, image_path_string = item
        image_path = Path(image_path_string)
        progress_value = completed_before_start + pending_index + 1

        processing_queue.put(
            (
                "processing",
                row_index,
                pending_index,
                total_pending,
                image_path.name,
            )
        )

        try:
            result = identify_part(image_path)

            if result is None:
                processing_queue.put(
                    (
                        "failed",
                        row_index,
                        progress_value,
                        image_path.name,
                    )
                )
                continue

            append_result_to_csv(result)

            processing_queue.put(
                (
                    "complete",
                    row_index,
                    progress_value,
                    str(image_path),
                    result,
                )
            )

        except Exception as error:
            print(
                f"Background processing error for "
                f"'{image_path.name}': {error}"
            )

            processing_queue.put(
                (
                    "failed",
                    row_index,
                    progress_value,
                    image_path.name,
                )
            )

    processing_queue.put(
        (
            "finished",
            total_pending,
        )
    )


# FUNCTION GUIDE: check_processing_queue
# Purpose: Read worker messages and safely update the interface.
def check_processing_queue():
    """Read worker messages and safely update the interface."""

    batch_finished = False

    try:
        while True:
            message = processing_queue.get_nowait()
            message_type = message[0]

            if message_type == "processing":
                (
                    _,
                    row_index,
                    pending_index,
                    total_pending,
                    image_name,
                ) = message

                processing_status_label.configure(
                    text=(
                        f"Processing new image {pending_index + 1} of "
                        f"{total_pending}: {image_name}"
                    ),
                    foreground="#1D4ED8",
                )

                set_image_list_row(
                    row_index,
                    selected_image_paths[row_index],
                    "processing",
                )

            elif message_type == "failed":
                _, row_index, progress_value, image_name = message
                failed_image_paths.add(str(Path(selected_image_paths[row_index])))
                update_batch_summary()

                set_image_list_row(
                    row_index,
                    selected_image_paths[row_index],
                    "failed",
                )

                batch_progress["value"] = progress_value

            elif message_type == "complete":
                (
                    _,
                    row_index,
                    progress_value,
                    image_path,
                    result,
                ) = message

               # Apply any previously saved employee reviews immediately.
                apply_saved_part_review(result)
                apply_saved_color_review(result)

                try:
                    if not sync_reviewed_result_to_csv(result):
                        queue_csv_review(result)
                except OSError as error:
                    print(f"CSV review update error: {error}")
                    queue_csv_review(result)

                # A repeated Part ID can be legitimate inventory. Warn the
                # employee, but never block or discard the additional item.
                repeated_part_images = [
                    Path(saved_path).name
                    for saved_path, saved_result in processed_results.items()
                    if (
                        saved_result.get("Part ID")
                        == result.get("Part ID")
                        and Path(saved_path).name
                        != Path(image_path).name
                    )
                ]

                if repeated_part_images:
                    duplicate_part_warnings.append(
                        (
                            Path(image_path).name,
                            result.get("Part ID", "Unknown"),
                            repeated_part_images,
                        )
                    )

                # Store the new result and count only work performed during
                # this active batch session.
                failed_image_paths.discard(image_path)
                processed_results[image_path] = result
                newly_processed_paths.add(image_path)

                update_batch_summary()

                review_required = (
                    result["Part Review Required"] == "Yes"
                    or result["Color Review Required"] == "Yes"
                )

                image_name = Path(image_path).name

                set_image_list_row(
                    row_index,
                    image_path,
                    "review" if review_required else "done",
                )

                batch_progress["value"] = progress_value
                show_save_confirmation(
                    f"CSV and JSON result for {image_name}"
                )

            elif message_type == "finished":
                _, processed_now = message
                batch_finished = True
                reused_saved = sum(
                    1
                    for image_path in selected_image_paths
                    if str(Path(image_path)) in previously_saved_paths
                )

                upload_button.configure(state="normal")
                folder_upload_button.configure(state="normal")
                process_button.configure(state="normal")
                update_excel_export_state()
                image_filter_dropdown.configure(state="readonly")

                processing_status_label.configure(
                    text=(
                        f"Batch complete: {processed_now} new "
                        f"image(s) processed; {reused_saved} saved "
                        "result(s) reused."
                    ),
                    foreground="#15803D",
                )

                if duplicate_part_warnings:
                    warning_lines = []

                    for (
                        image_name,
                        part_id,
                        matching_images,
                    ) in duplicate_part_warnings[:10]:
                        warning_lines.append(
                            f"• {image_name}: Part ID {part_id} also "
                            f"appears in {', '.join(matching_images)}"
                        )

                    messagebox.showwarning(
                        "Repeated Part IDs Found",
                        (
                            "One or more identified parts already appear "
                            "in this batch. This may be correct when the "
                            "inventory contains multiple copies.\n\n"
                            + "\n".join(warning_lines)
                        ),
                    )

                image_filter_dropdown.set("Needs attention")
                apply_image_filter()
                update_batch_summary()

    except queue.Empty:
        pass

    if not batch_finished:
        window.after(100, check_processing_queue)

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")

window = ctk.CTk()
window.configure(fg_color=APP_BACKGROUND)
window.title("SK BrickRescue Part Recognition")

# Keep the full workspace hidden while its widgets are being assembled. A
# small branded splash screen gives the employee immediate visual feedback
# instead of briefly showing a partially drawn application window.
window.withdraw()

splash_window = ctk.CTkToplevel(window)
splash_window.overrideredirect(True)
splash_window.configure(fg_color=APP_BACKGROUND)
splash_window.attributes("-topmost", True)

splash_width = 520
splash_height = 315
splash_x = (splash_window.winfo_screenwidth() - splash_width) // 2
splash_y = (splash_window.winfo_screenheight() - splash_height) // 2
splash_window.geometry(
    f"{splash_width}x{splash_height}+{splash_x}+{splash_y}"
)

splash_card = ctk.CTkFrame(
    splash_window,
    fg_color=CARD_BACKGROUND,
    corner_radius=18,
    border_width=1,
    border_color="#D7DEE5",
)
splash_card.pack(
    expand=True,
    fill="both",
    padx=12,
    pady=12,
)

if LOGO_FILE.exists():
    with Image.open(LOGO_FILE) as splash_logo_source:
        splash_logo_pil = splash_logo_source.copy()

    splash_logo = ctk.CTkImage(
        light_image=splash_logo_pil,
        dark_image=splash_logo_pil,
        size=(105, 105),
    )
    splash_logo_label = ctk.CTkLabel(
        splash_card,
        text="",
        image=splash_logo,
    )
    splash_logo_label.image = splash_logo
    splash_logo_label.pack(pady=(24, 4))

ctk.CTkLabel(
    splash_card,
    text="SK BrickRescue",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=27,
        weight="bold",
    ),
    text_color=TEXT_PRIMARY,
).pack()

ctk.CTkLabel(
    splash_card,
    text="LEGO Part Recognition & Inventory Support",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=13,
    ),
    text_color=TEXT_SECONDARY,
).pack(pady=(2, 18))

splash_progress = ctk.CTkProgressBar(
    splash_card,
    width=350,
    height=10,
    corner_radius=5,
    mode="indeterminate",
    progress_color=PRIMARY_GREEN,
    fg_color="#E5EAF0",
)
splash_progress.pack()
splash_progress.start()

ctk.CTkLabel(
    splash_card,
    text="Preparing your workspace...",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
    ),
    text_color=TEXT_SECONDARY,
).pack(pady=(10, 0))

# Draw the splash immediately before constructing the larger interface.
splash_window.update_idletasks()
splash_window.update()

icon_path = PROJECT_ROOT / "assets" / "sk_brickrescue.ico"

if icon_path.exists():
    window.iconbitmap(str(icon_path))
fit_window_to_screen(
    window,
    1050,
    720,
    minimum_width=820,
    minimum_height=580,
    horizontal_margin=40,
    vertical_margin=70,
)

# Traditional desktop menu for secondary application commands. The colorful
# workspace remains unchanged, while utilities are organized like familiar
# internal company software.
application_menu = tk.Menu(
    window,
    tearoff=False,
    font=("Segoe UI", 10),
)

file_menu = tk.Menu(
    application_menu,
    tearoff=False,
    font=("Segoe UI", 10),
)
file_menu.add_command(
    label="Select LEGO Images...",
    command=upload_images,
)
file_menu.add_command(
    label="Open Results Folder",
    command=open_results_folder,
)
file_menu.add_separator()
file_menu.add_command(
    label="Remove Selected Image",
    command=remove_selected_image,
)
file_menu.add_command(
    label="Clear Current Batch",
    command=clear_current_batch,
)
file_menu.add_separator()
file_menu.add_command(
    label="Exit",
    command=quit_application,
)
application_menu.add_cascade(
    label="File",
    menu=file_menu,
)

batch_menu = tk.Menu(
    application_menu,
    tearoff=False,
    font=("Segoe UI", 10),
)
batch_menu.add_command(
    label="Process All Images",
    command=start_batch_processing,
)
batch_menu.add_command(
    label="Update All Approval Statuses",
    command=update_all_approval_statuses,
)
batch_menu.add_separator()
batch_menu.add_command(
    label="Start New Batch...",
    command=start_new_batch,
)
application_menu.add_cascade(
    label="Batch",
    menu=batch_menu,
)

reports_menu = tk.Menu(
    application_menu,
    tearoff=False,
    font=("Segoe UI", 10),
)
reports_menu.add_command(
    label="Export Excel Report",
    command=export_excel_report,
)
application_menu.add_cascade(
    label="Reports",
    menu=reports_menu,
)

help_menu = tk.Menu(
    application_menu,
    tearoff=False,
    font=("Segoe UI", 10),
)
help_menu.add_command(
    label="Workflow Guide",
    command=show_workflow_guide,
)
help_menu.add_separator()
help_menu.add_command(
    label="About SK BrickRescue Part Recognition",
    command=show_about_dialog,
)
application_menu.add_cascade(
    label="Help",
    menu=help_menu,
)

window.configure(menu=application_menu)

# Main scrollable container for the complete application.
# This keeps lower controls accessible on smaller screens.
app_scroll_frame = ctk.CTkScrollableFrame(
    window,
    fg_color=APP_BACKGROUND,
    corner_radius=0,
)
app_scroll_frame.pack(
    expand=True,
    fill="both",
)

# ------------------------------
# Processing toolbar
# ------------------------------

toolbar_frame = ctk.CTkFrame(
    app_scroll_frame,
    fg_color=CARD_BACKGROUND,
    corner_radius=14,
    border_width=1,
    border_color="#D9DEE3",
)
toolbar_frame.pack(
    fill="x",
    padx=12,
    pady=(0, 5),
)

toolbar_frame.grid_columnconfigure(0, weight=1)
toolbar_frame.grid_columnconfigure(1, weight=1)

primary_action_frame = ctk.CTkFrame(
    toolbar_frame,
    fg_color="transparent",
)
primary_action_frame.grid(
    row=0,
    column=0,
    columnspan=2,
    pady=(6, 3),
)

# Draw a small spreadsheet symbol in memory so the export action is easy to
# recognize without relying on a platform-specific emoji or external asset.
excel_icon_source = Image.new("RGBA", (30, 30), (0, 0, 0, 0))
excel_icon_draw = ImageDraw.Draw(excel_icon_source)
excel_icon_draw.rounded_rectangle(
    (2, 2, 28, 28),
    radius=4,
    outline="white",
    width=2,
)
excel_icon_draw.rectangle((14, 6, 25, 24), outline="white", width=1)
excel_icon_draw.line((14, 12, 25, 12), fill="white", width=1)
excel_icon_draw.line((14, 18, 25, 18), fill="white", width=1)
excel_icon_draw.line((19, 6, 19, 24), fill="white", width=1)
excel_icon_draw.line((8, 9, 14, 21), fill="white", width=3)
excel_icon_draw.line((14, 9, 8, 21), fill="white", width=3)
excel_export_icon = ctk.CTkImage(
    light_image=excel_icon_source,
    dark_image=excel_icon_source,
    size=(24, 24),
)

excel_icon_disabled_source = Image.new("RGBA", (30, 30), (0, 0, 0, 0))
excel_icon_disabled_draw = ImageDraw.Draw(excel_icon_disabled_source)
excel_icon_disabled_draw.rounded_rectangle(
    (2, 2, 28, 28),
    radius=4,
    outline="#F1F5F3",
    width=2,
)
excel_icon_disabled_draw.rectangle(
    (14, 6, 25, 24),
    outline="#F1F5F3",
    width=1,
)
excel_icon_disabled_draw.line(
    (8, 9, 14, 21),
    fill="#F1F5F3",
    width=3,
)
excel_icon_disabled_draw.line(
    (14, 9, 8, 21),
    fill="#F1F5F3",
    width=3,
)
excel_export_icon_disabled = ctk.CTkImage(
    light_image=excel_icon_disabled_source,
    dark_image=excel_icon_disabled_source,
    size=(24, 24),
)

upload_button = ttk.Button(
    primary_action_frame,
    text="Select LEGO Images",
    command=upload_images,
)
upload_button.pack(
    side="left",
    padx=(0, 4),
)

folder_upload_button = ttk.Button(
    primary_action_frame,
    text="Add Image Folders",
    command=upload_image_folders,
)
folder_upload_button.pack(
    side="left",
    padx=4,
)

process_button = ttk.Button(
    primary_action_frame,
    text="Process All Images",
    command=start_batch_processing,
)
process_button.pack(
    side="left",
    padx=4,
)

update_all_statuses_button = ttk.Button(
    primary_action_frame,
    text="Update All Statuses (0)",
    command=update_all_approval_statuses,
    state="disabled",
)
update_all_statuses_button.pack(
    side="left",
    padx=(4, 0),
)

image_count_label = ctk.CTkLabel(
    toolbar_frame,
    text="No images selected",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
    ),
    text_color=TEXT_SECONDARY,
)
image_count_label.grid(
    row=1,
    column=0,
    padx=(25, 10),
    pady=(0, 4),
    sticky="e",
)

processing_status_label = tk.Label(
    toolbar_frame,
    text="Select an image to begin.",
    font=("Segoe UI", 10),
    foreground="#666666",
    background=CARD_BACKGROUND,
)
processing_status_label.grid(
    row=1,
    column=1,
    padx=(10, 25),
    pady=(0, 4),
    sticky="w",
)

batch_progress = ttk.Progressbar(
    toolbar_frame,
    orient="horizontal",
    mode="determinate",
)
batch_progress.grid(
    row=2,
    column=0,
    columnspan=2,
    padx=25,
    pady=(0, 6),
    sticky="ew",
)
save_activity_label = ctk.CTkLabel(
    toolbar_frame,
    text="No saves recorded in this session yet.",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=11,
    ),
    text_color=TEXT_SECONDARY,
)
save_activity_label.grid(
    row=3,
    column=0,
    columnspan=2,
    pady=(0, 5),
)


# ------------------------------
# Current batch summary
# ------------------------------

batch_summary_frame = ctk.CTkFrame(
    toolbar_frame,
    fg_color="#F7F9FA",
    corner_radius=10,
)

batch_summary_frame.grid(
    row=4,
    column=0,
    columnspan=2,
    padx=25,
    pady=(0, 7),
    sticky="ew",
)

for column in range(5):
    batch_summary_frame.grid_columnconfigure(
        column,
        weight=1,
    )

selected_summary_label = ctk.CTkLabel(
    batch_summary_frame,
    text="Selected\n0",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
        weight="bold",
    ),
    text_color=TEXT_PRIMARY,
)
selected_summary_label.grid(
    row=0,
    column=0,
    padx=10,
    pady=5,
)

processed_summary_label = ctk.CTkLabel(
    batch_summary_frame,
    text="Newly Processed\n0",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
        weight="bold",
    ),
    text_color=TEXT_PRIMARY,
)
processed_summary_label.grid(
    row=0,
    column=1,
    padx=10,
    pady=5,
)

ready_summary_label = ctk.CTkLabel(
    batch_summary_frame,
    text="Ready\n0",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
        weight="bold",
    ),
    text_color=CLEAR_BADGE_TEXT,
)
ready_summary_label.grid(
    row=0,
    column=2,
    padx=10,
    pady=5,
)

part_review_summary_label = ctk.CTkLabel(
    batch_summary_frame,
    text="Part Check\n0",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
        weight="bold",
    ),
    text_color=REVIEW_BADGE_TEXT,
)
part_review_summary_label.grid(
    row=0,
    column=3,
    padx=10,
    pady=5,
)

color_review_summary_label = ctk.CTkLabel(
    batch_summary_frame,
    text="Color Check\n0",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
        weight="bold",
    ),
    text_color=REVIEW_BADGE_TEXT,
)
color_review_summary_label.grid(
    row=0,
    column=4,
    padx=10,
    pady=5,
)

workspace_frame = ctk.CTkFrame(
    app_scroll_frame,
    height=300,
    fg_color="transparent",
)
workspace_frame.pack(
    expand=True,
    fill="both",
    padx=18,
    pady=(0, 7),
)
workspace_frame.pack_propagate(False)

# ------------------------------
# Selected-image list
# ------------------------------

list_panel = ctk.CTkFrame(
    workspace_frame,
    width=220,
    fg_color=CARD_BACKGROUND,
    corner_radius=12,
    border_width=1,
    border_color="#D9DEE3",
)
list_panel.pack(
    side="left",
    fill="y",
    padx=(0, 15),
)
list_panel.pack_propagate(False)

# The right side contains only the two previews. Review controls are packed as
# a separate row immediately below the fixed-height workspace.
preview_column = ttk.Frame(workspace_frame)
preview_column.pack(side="left", expand=True, fill="both")

image_preview_row = ttk.Frame(preview_column)
image_preview_row.pack(expand=True, fill="both")

list_header = ctk.CTkFrame(
    list_panel,
    fg_color="transparent",
)
list_header.pack(
    fill="x",
    padx=12,
    pady=(12, 6),
)

list_title = ctk.CTkLabel(
    list_header,
    text="Selected Images",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=15,
        weight="bold",
    ),
    text_color=TEXT_PRIMARY,
)
list_title.pack(
    side="left",
    expand=True,
    padx=(26, 0),
)

remove_selected_button = ctk.CTkButton(
    list_header,
    text="×",
    command=remove_selected_image,
    width=26,
    height=26,
    corner_radius=7,
    fg_color="#E5E7EB",
    hover_color="#FECACA",
    text_color="#991B1B",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=17,
        weight="bold",
    ),
)
remove_selected_button.pack(side="right")


image_filter_dropdown = ctk.CTkComboBox(
    list_panel,
    values=[
        "Needs attention",
        "All Images",
        "Suggested Checks",
        "Part Check",
        "Color Check",
        "Ready",
    ],
    command=apply_image_filter,
    state="readonly",
    width=190,
    height=32,
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
    ),
)

image_filter_dropdown.set("Needs attention")

image_filter_dropdown.pack(
    padx=12,
    pady=(0, 5),
)

image_status_legend = ctk.CTkLabel(
    list_panel,
    text="✓ Verified   ⚠ Review   ↻ Saved / unverified",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=10,
    ),
    text_color=TEXT_SECONDARY,
)
image_status_legend.pack(
    padx=8,
    pady=(0, 7),
)

# Holds the image list and its vertical scrollbar.
listbox_container = tk.Frame(
    list_panel,
    background="#F7F9FA",
)
listbox_container.pack(
    expand=True,
    fill="both",
    padx=12,
    pady=(0, 15),
)

listbox_container.grid_rowconfigure(0, weight=1)
listbox_container.grid_columnconfigure(0, weight=1)

image_listbox = tk.Listbox(
    listbox_container,
    font=("Segoe UI", 10),
    activestyle="none",
    exportselection=False,
    background="#F7F9FA",
    foreground=TEXT_PRIMARY,
    selectbackground=PRIMARY_GREEN,
    selectforeground="#FFFFFF",
    relief="flat",
    borderwidth=0,
    highlightthickness=0,
)

image_list_scrollbar = ttk.Scrollbar(
    listbox_container,
    orient="vertical",
    command=image_listbox.yview,
)

image_listbox.configure(
    yscrollcommand=image_list_scrollbar.set,
)

image_listbox.grid(
    row=0,
    column=0,
    sticky="nsew",
)

image_list_scrollbar.grid(
    row=0,
    column=1,
    sticky="ns",
)

image_listbox.bind(
    "<<ListboxSelect>>",
    show_selected_image,
)

# ------------------------------
# Uploaded-image preview
# ------------------------------

uploaded_panel = ctk.CTkFrame(
    image_preview_row,
    fg_color=CARD_BACKGROUND,
    corner_radius=12,
    border_width=1,
    border_color="#D9DEE3",
)
uploaded_panel.pack(
    side="left",
    expand=True,
    fill="both",
    padx=(0, 8),
)

uploaded_title = ctk.CTkLabel(
    uploaded_panel,
    text="Uploaded Image",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=16,
        weight="bold",
    ),
    text_color=TEXT_PRIMARY,
)
uploaded_title.pack(pady=(7, 1))

selected_file_label = ctk.CTkLabel(
    uploaded_panel,
    text="No image selected",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
    ),
    text_color=TEXT_SECONDARY,
)
selected_file_label.pack(pady=(0, 4))

uploaded_preview_frame = tk.Frame(
    uploaded_panel,
    background="white",
    highlightbackground="#B8B8B8",
    highlightthickness=1,
)
uploaded_preview_frame.pack(
    expand=True,
    fill="both",
    padx=10,
    pady=(0, 8),
)

uploaded_image_preview = tk.Label(
    uploaded_preview_frame,
    text="Select one or more LEGO images",
    font=("Segoe UI", 11),
    foreground="#666666",
    background="white",
    cursor="hand2",
)
uploaded_image_preview.pack(
    expand=True,
    fill="both",
    padx=7,
    pady=7,
)
uploaded_image_preview.bind(
    "<Button-1>",
    lambda _event: open_enlarged_preview("uploaded"),
)

# ------------------------------
# Brickognize/Bricklink reference placeholder
# ------------------------------

reference_panel = ctk.CTkFrame(
    image_preview_row,
    fg_color=CARD_BACKGROUND,
    corner_radius=12,
    border_width=1,
    border_color="#D9DEE3",
)
reference_panel.pack(
    side="left",
    expand=True,
    fill="both",
    padx=(8, 0),
)

reference_title = ctk.CTkLabel(
    reference_panel,
    text="BrickLink Catalog Reference",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=16,
        weight="bold",
    ),
    text_color=TEXT_PRIMARY,
)
reference_title.pack(pady=(7, 1))

reference_subtitle = ctk.CTkLabel(
    reference_panel,
    text="Catalog match  •  Click image to enlarge",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
    ),
    text_color=TEXT_SECONDARY,
)
reference_subtitle.pack(pady=(0, 4))

reference_preview_frame = tk.Frame(
    reference_panel,
    background="white",
    highlightbackground="#B8B8B8",
    highlightthickness=1,
)
reference_preview_frame.pack(
    expand=True,
    fill="both",
    padx=10,
    pady=(0, 8),
)

reference_image_preview = tk.Label(
    reference_preview_frame,
    text="Reference image will appear\nafter processing",
    font=("Segoe UI", 11),
    foreground="#666666",
    background="white",
    justify="center",
    cursor="hand2",
)
reference_image_preview.pack(
    expand=True,
    fill="both",
    padx=7,
    pady=7,
)
reference_image_preview.bind(
    "<Button-1>",
    lambda _event: open_enlarged_preview("reference"),
)

# ------------------------------
# Selected identification details
# ------------------------------

results_frame = ctk.CTkFrame(
    app_scroll_frame,
    fg_color=CARD_BACKGROUND,
    corner_radius=12,
    border_width=1,
    border_color="#D9DEE3",
)
results_frame.pack(
    fill="x",
    padx=18,
    pady=(0, 8),
)

results_frame.grid_columnconfigure(1, weight=1)
results_frame.grid_columnconfigure(3, weight=1)
results_frame.grid_columnconfigure(5, weight=1)
results_frame.grid_columnconfigure(7, weight=1)

results_title = ctk.CTkLabel(
    results_frame,
    text="Identification Results",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=16,
        weight="bold",
    ),
    text_color=TEXT_PRIMARY,
)
results_title.grid(
    row=0,
    column=0,
    columnspan=6,
    pady=(12, 10),
)
results_title.grid_remove()

part_section_title = ctk.CTkLabel(
    results_frame,
    text="Part Identification",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
        weight="bold",
    ),
    text_color=TEXT_SECONDARY,
)
part_section_title.grid(
    row=1,
    column=0,
    columnspan=6,
    padx=20,
    pady=(0, 8),
    sticky="w",
)
part_section_title.grid_remove()

part_name_label = tk.Label(
    results_frame,
    text="Part Name",
    font=("Segoe UI", 10, "bold"),
    background=CARD_BACKGROUND,
)
part_name_label.grid(
    row=0,
    column=0,
    padx=(20, 8),
    pady=(9, 8),
    sticky="w",
)

part_name_value = tk.Label(
    results_frame,
    text="-",
    font=("Segoe UI", 10),
    background=CARD_BACKGROUND,
    wraplength=500,
    justify="left"
)
part_name_value.grid(
    row=0,
    column=1,
    padx=(0, 35),
    pady=(9, 8),
    sticky="w",
)

part_id_label = tk.Label(
    results_frame,
    text="Part ID",
    font=("Segoe UI", 10, "bold"),
    background=CARD_BACKGROUND,
)
part_id_label.grid(
    row=0,
    column=2,
    padx=(0, 8),
    pady=(9, 8),
    sticky="w",
)

part_id_value = tk.Label(
    results_frame,
    text="-",
    font=("Segoe UI", 10),
    background=CARD_BACKGROUND,
)
part_id_value.grid(
    row=0,
    column=3,
    padx=(0, 20),
    pady=(9, 8),
    sticky="w",
)

color_section_title = ctk.CTkLabel(
    results_frame,
    text="Color Identification",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
        weight="bold",
    ),
    text_color=TEXT_SECONDARY,
)
color_section_title.grid(
    row=4,
    column=0,
    columnspan=6,
    padx=20,
    pady=(2, 8),
    sticky="w",
)
color_section_title.grid_remove()

lego_color_label = tk.Label(
    results_frame,
    text="LEGO Color",
    font=("Segoe UI", 10, "bold"),
    background=CARD_BACKGROUND,
)
lego_color_label.grid(
    row=1,
    column=0,
    padx=(20, 8),
    pady=(0, 8),
    sticky="w",
)

lego_color_value = tk.Label(
    results_frame,
    text="-",
    font=("Segoe UI", 10),
    background=CARD_BACKGROUND,
)
lego_color_value.grid(
    row=1,
    column=1,
    padx=(0, 35),
    pady=(0, 8),
    sticky="w",
)

part_confidence_label = tk.Label(
    results_frame,
    text="Part Confidence",
    font=("Segoe UI", 10, "bold"),
    background=CARD_BACKGROUND,
)
part_confidence_label.grid(
    row=0,
    column=4,
    padx=(0, 8),
    pady=(9, 8),
    sticky="w",
)

part_confidence_value = ctk.CTkLabel(
    results_frame,
    text="-",
    width=78,
    height=24,
    corner_radius=12,
    fg_color=NEUTRAL_BADGE_BACKGROUND,
    text_color=NEUTRAL_BADGE_TEXT,
    font=ctk.CTkFont(
        family="Segoe UI",
        size=11,
        weight="bold",
    ),
)
part_confidence_value.grid(
    row=0,
    column=5,
    padx=(0, 35),
    pady=(9, 8),
    sticky="w",
)

color_confidence_label = tk.Label(
    results_frame,
    text="Color Confidence",
    font=("Segoe UI", 10, "bold"),
    background=CARD_BACKGROUND,
)
color_confidence_label.grid(
    row=1,
    column=2,
    padx=(0, 8),
    pady=(0, 8),
    sticky="w",
)

color_confidence_value = ctk.CTkLabel(
    results_frame,
    text="-",
    width=78,
    height=24,
    corner_radius=12,
    fg_color=NEUTRAL_BADGE_BACKGROUND,
    text_color=NEUTRAL_BADGE_TEXT,
    font=ctk.CTkFont(
        family="Segoe UI",
        size=11,
        weight="bold",
    ),
)
color_confidence_value.grid(
    row=1,
    column=3,
    padx=(0, 20),
    pady=(0, 8),
    sticky="w",
)

part_review_label = tk.Label(
    results_frame,
    text="Part Check Suggested",
    font=("Segoe UI", 10, "bold"),
    background=CARD_BACKGROUND,
)
part_review_label.grid(
    row=0,
    column=6,
    padx=(0, 8),
    pady=(9, 8),
    sticky="w",
)

part_review_value = ctk.CTkLabel(
    results_frame,
    text="-",
    width=54,
    height=24,
    corner_radius=12,
    fg_color=NEUTRAL_BADGE_BACKGROUND,
    text_color=NEUTRAL_BADGE_TEXT,
    font=ctk.CTkFont(
        family="Segoe UI",
        size=11,
        weight="bold",
    ),
)
part_review_value.grid(
    row=0,
    column=7,
    padx=(0, 20),
    pady=(9, 8),
    sticky="w",
)

rescan_part_button = ctk.CTkButton(
    results_frame,
    text="Rescan Part",
    command=select_rescan_image,
    width=120,
    height=32,
    corner_radius=8,
    fg_color=PRIMARY_GREEN,
    hover_color=PRIMARY_GREEN_HOVER,
)
# The action is positioned by display_result_details only after the selected
# image has a processed result.
rescan_part_button.grid_remove()

part_save_status_label = ctk.CTkLabel(
    results_frame,
    text="",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=11,
        weight="bold",
    ),
    text_color=TEXT_SECONDARY,
)
part_save_status_label.grid_remove()

color_review_label = tk.Label(
    results_frame,
    text="Color Check Suggested",
    font=("Segoe UI", 10, "bold"),
    background=CARD_BACKGROUND,
)
color_review_label.grid(
    row=1,
    column=4,
    padx=(0, 8),
    pady=(0, 8),
    sticky="w",
)

color_review_value = ctk.CTkLabel(
    results_frame,
    text="-",
    width=54,
    height=24,
    corner_radius=12,
    fg_color=NEUTRAL_BADGE_BACKGROUND,
    text_color=NEUTRAL_BADGE_TEXT,
    font=ctk.CTkFont(
        family="Segoe UI",
        size=11,
        weight="bold",
    ),
)
color_review_value.grid(
    row=1,
    column=5,
    padx=(0, 35),
    pady=(0, 8),
    sticky="w",
)

# ------------------------------
# Manual color review controls
# ------------------------------

color_review_controls = ctk.CTkFrame(
    results_frame,
    fg_color="#FFF7D6",
    corner_radius=8,
)

color_review_prompt = ctk.CTkLabel(
    color_review_controls,
    text="Confirm LEGO Color",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=12,
        weight="bold",
    ),
    text_color=REVIEW_BADGE_TEXT,
)
color_review_prompt.pack(
    side="left",
    padx=(12, 8),
    pady=8,
)

color_review_dropdown = ctk.CTkComboBox(
    color_review_controls,
    values=sorted(BRICKLINK_REVIEW_COLORS.keys()),
    command=preview_selected_color,
    width=190,
    state="readonly",
)
color_review_dropdown.pack(
    side="left",
    padx=(0, 12),
    pady=8,
)

confirm_color_button = ctk.CTkButton(
    color_review_controls,
    text="Confirm Color",
    command=confirm_selected_color,
    width=120,
    height=32,
    corner_radius=8,
    fg_color=PRIMARY_GREEN,
    hover_color=PRIMARY_GREEN_HOVER,
)
confirm_color_button.pack(
    side="left",
    padx=(0, 10),
    pady=8,
)

color_save_status_label = ctk.CTkLabel(
    color_review_controls,
    text="Choose a color, then save",
    font=ctk.CTkFont(
        family="Segoe UI",
        size=11,
        weight="bold",
    ),
    text_color=TEXT_SECONDARY,
)
color_save_status_label.pack(
    side="left",
    padx=(0, 12),
    pady=8,
)

# ------------------------------
# Result action buttons
# ------------------------------

results_action_frame = ctk.CTkFrame(
    results_frame,
    fg_color="transparent",
)
results_action_frame.grid(
    row=4,
    column=0,
    columnspan=8,
    padx=(20, 8),
    pady=(0, 8),
    sticky="w",
)

# Keep review actions outside the fixed-height preview workspace. Large source
# images can resize within their panels without covering or clipping this row.
review_navigation_frame = ttk.Frame(app_scroll_frame, padding=(0, 2, 0, 5))
review_navigation_frame.pack(
    fill="x",
    padx=18,
    before=results_frame,
)

review_actions = (
    ("Previous", lambda: move_review_selection(-1)),
    ("Approve & Next", approve_current_and_next),
    ("Similar Part", choose_similar_part),
    ("Rescan", select_rescan_image),
    ("Change Color", show_color_choices),
    ("Delete", delete_selected_part),
    ("Add Images", add_part_images),
)
for column, (button_text, button_command) in enumerate(review_actions):
    review_navigation_frame.columnconfigure(column, weight=1)
    ttk.Button(
        review_navigation_frame,
        text=button_text,
        command=button_command,
    ).grid(row=0, column=column, sticky="ew", padx=2)

excel_export_button = ctk.CTkButton(
    results_action_frame,
    text="",
    image=excel_export_icon_disabled,
    command=export_excel_report,
    width=42,
    height=36,
    corner_radius=8,
    state="disabled",
    fg_color="#CBD5D1",
    hover_color="#CBD5D1",
)
excel_export_button.pack(
    side="left",
    padx=(0, 8),
)

results_folder_button = ttk.Button(
    results_action_frame,
    text="Open Results Folder",
    command=open_results_folder,
)
results_folder_button.pack(
    side="left",
    padx=(0, 4),
)

clear_batch_button = ttk.Button(
    results_action_frame,
    text="Batch Options",
    command=start_new_batch,
)
clear_batch_button.pack(
    side="left",
    padx=(0, 4),
)

bricklink_button = ttk.Button(
    results_action_frame,
    text="Open BrickLink",
    command=open_bricklink,
)
bricklink_button.pack(
    side="left",
    padx=(0, 8),
)

complete_batch_button = ttk.Button(
    results_action_frame,
    text="Complete Batch",
    command=complete_current_batch,
)
complete_batch_button.pack(
    side="left",
)

# Concise hover help keeps the compact workspace understandable without
# permanently adding more instructional text to the screen.
add_tooltip(
    upload_button,
    "Select one or more JPG, JPEG, or PNG LEGO photographs. Selecting a new group replaces only the visible queue.",
)
add_tooltip(
    process_button,
    "Process every new image in the active queue. Matching saved filenames are reused and marked with ↻.",
)
add_tooltip(
    save_activity_label,
    "Shows the most recent successful file save and its timestamp. Failed actions never update this message.",
)
add_tooltip(
    selected_summary_label,
    "Number of photographs currently loaded in the visible working set.",
)
add_tooltip(
    processed_summary_label,
    "Number of images newly processed during this application session; reused saved results are excluded.",
)
add_tooltip(
    ready_summary_label,
    "Processed results that have no low-confidence part or color check suggestion.",
)
add_tooltip(
    part_review_summary_label,
    "Results where confidence suggests that an optional part rescan may be useful.",
)
add_tooltip(
    color_review_summary_label,
    "Results where confidence suggests that an optional color verification may be useful.",
)
add_tooltip(
    remove_selected_button,
    "Remove only the highlighted row from the screen. The source image and saved result files are not deleted.",
)
add_tooltip(
    image_filter_dropdown,
    "Filter the active queue by all images, suggested checks, part checks, color checks, or Ready results.",
)
add_tooltip(
    image_status_legend,
    "✓ Ready  •  ⚠ Check suggested  •  ↻ Saved filename/result reused.",
)
add_tooltip(
    image_listbox,
    "Select a filename to display its full photograph, catalog reference, identification, and available actions.",
)
add_tooltip(
    uploaded_image_preview,
    "Shows the complete uploaded photograph without cropping. Click to open a larger proportional view.",
)
add_tooltip(
    reference_image_preview,
    "Shows the complete BrickLink catalog reference without cropping. Click to open a larger proportional view.",
)
add_tooltip(
    part_confidence_value,
    "Brickognize confidence in the predicted LEGO Part ID. This is guidance, not a forced decision.",
)
add_tooltip(
    color_confidence_value,
    "Confidence in the predicted LEGO color. Employees may verify or change any processed result.",
)
add_tooltip(
    part_review_label,
    "YES suggests checking the part from another angle; rescanning remains optional.",
)
add_tooltip(
    color_review_label,
    "YES suggests verifying the color; the color selector remains available for every processed result.",
)
add_tooltip(
    rescan_part_button,
    "Open the camera-first rescan window. Take a new photo or upload one, compare both results, then choose the final Part ID.",
)
add_tooltip(
    part_save_status_label,
    "Confirms the selected rescan photo and the final saved Part ID after the comparison decision.",
)
add_tooltip(
    color_review_dropdown,
    "Choose a supported BrickLink color. The catalog preview changes immediately, but the choice is not stored until Save Color is selected.",
)
add_tooltip(
    confirm_color_button,
    "Approve the displayed color in memory. Use Update All Statuses when the review pass is finished.",
)
add_tooltip(
    color_save_status_label,
    "Shows whether the selected color is waiting for the batch status update.",
)
add_tooltip(
    update_all_statuses_button,
    "Write all queued part and color approval statuses to the CSV together.",
)
add_tooltip(
    excel_export_button,
    "Create and open the formatted Excel report. Available only when active results are loaded and processing is finished.",
)
add_tooltip(
    results_folder_button,
    "Open the results folder containing current CSV, JSON, camera captures, Excel reports, and completed-set archives.",
)
add_tooltip(
    clear_batch_button,
    "Choose Clear Screen Only to continue the same set, or Finish & Start New LEGO Set to archive and separate its files.",
)
add_tooltip(
    complete_batch_button,
    "Save all pending verification changes, finish this Step 2 batch, and clear the workspace while keeping its results ready for Step 3.",
)
add_tooltip(
    bricklink_button,
    "Open the selected result's BrickLink catalog page using its current final part and color.",
)


def build_gallery_interface():
    """Replace the classic presentation with a scrollable, rapid-review gallery."""

    window.title("SK BrickRescue Part Recognition — Gallery Review")
    app_scroll_frame.pack_forget()

    gallery_root = ttk.Frame(window, padding=10)
    gallery_root.pack(fill="both", expand=True)
    gallery_root.columnconfigure(0, weight=7)
    gallery_root.columnconfigure(1, weight=3)
    gallery_root.rowconfigure(0, weight=1)

    left = ttk.LabelFrame(
        gallery_root,
        text="Scanned image and identified reference",
        padding=6,
    )
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
    left.rowconfigure(1, weight=1)
    left.columnconfigure(0, weight=1)

    page_size = 10
    page_state = {"start": 0}
    page_text = tk.StringVar(value="Images 0–0 of 0")
    page_bar = ttk.Frame(left)
    page_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))
    page_bar.columnconfigure(1, weight=1)
    previous_page_button = ttk.Button(
        page_bar,
        text="Previous 10",
        command=lambda: change_gallery_page(-page_size),
    )
    previous_page_button.grid(row=0, column=0, sticky="w")
    ttk.Label(page_bar, textvariable=page_text, anchor="center").grid(
        row=0, column=1, sticky="ew", padx=8
    )
    next_page_button = ttk.Button(
        page_bar,
        text="Next 10",
        command=lambda: change_gallery_page(page_size),
    )
    next_page_button.grid(row=0, column=2, sticky="e")

    gallery_canvas = tk.Canvas(left, background="#EEF1F4", highlightthickness=0)
    gallery_scrollbar = ttk.Scrollbar(
        left, orient="vertical", command=gallery_canvas.yview
    )
    gallery_canvas.configure(yscrollcommand=gallery_scrollbar.set)
    gallery_canvas.grid(row=1, column=0, sticky="nsew")
    gallery_scrollbar.grid(row=1, column=1, sticky="ns")
    gallery_rows = tk.Frame(gallery_canvas, background="#EEF1F4")
    gallery_window_id = gallery_canvas.create_window(
        (0, 0), window=gallery_rows, anchor="nw"
    )

    right = ttk.Frame(gallery_root, padding=(7, 0, 0, 0))
    right.grid(row=0, column=1, sticky="nsew")
    right.columnconfigure(0, weight=1)
    right.columnconfigure(1, weight=1)

    ttk.Label(right, text="Review controls", font=("Segoe UI", 15, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 7)
    )

    def action_button(row, column, text_value, command):
        button = ttk.Button(right, text=text_value, command=command)
        button.grid(row=row, column=column, sticky="ew", padx=2, pady=2)
        return button

    action_button(1, 0, "Select Images", upload_images)
    action_button(1, 1, "Add Folders", upload_image_folders)
    action_button(2, 0, "Process All", start_batch_processing)
    action_button(2, 1, "Save Statuses", update_all_approval_statuses)
    action_button(3, 0, "Previous", lambda: gallery_action(lambda: move_review_selection(-1)))
    action_button(3, 1, "Approve & Next", lambda: gallery_action(approve_current_and_next))
    action_button(4, 0, "Similar Part", lambda: gallery_action(choose_similar_part))
    action_button(4, 1, "Rescan", lambda: gallery_action(select_rescan_image))
    action_button(5, 0, "Change Color", lambda: gallery_action(show_color_choices))
    action_button(5, 1, "Delete", lambda: gallery_action(delete_selected_part))
    action_button(6, 0, "Save Color", lambda: gallery_action(confirm_selected_color))
    action_button(6, 1, "Add Image", add_part_images)
    action_button(7, 0, "Open BrickLink", open_bricklink)
    action_button(7, 1, "Results Folder", open_results_folder)
    action_button(8, 0, "Batch Options", start_new_batch)
    action_button(8, 1, "Complete Batch", lambda: gallery_action(complete_current_batch))

    gallery_processing_status = ttk.Label(
        right,
        text="Select images or folders to begin.",
        foreground="#475569",
        wraplength=300,
    )
    gallery_processing_status.grid(
        row=9,
        column=0,
        columnspan=2,
        sticky="ew",
        padx=2,
        pady=(9, 3),
    )
    gallery_progress = ttk.Progressbar(
        right,
        orient="horizontal",
        mode="determinate",
    )
    gallery_progress.grid(
        row=10,
        column=0,
        columnspan=2,
        sticky="ew",
        padx=2,
        pady=(0, 6),
    )

    ttk.Separator(right).grid(
        row=11, column=0, columnspan=2, sticky="ew", pady=9
    )
    ttk.Label(right, text="Selected identification", font=("Segoe UI", 13, "bold")).grid(
        row=12, column=0, columnspan=2, sticky="w", pady=(0, 6)
    )

    detail_variables = {
        "Image": tk.StringVar(value="Select an image"),
        "Part": tk.StringVar(value="—"),
        "Part ID": tk.StringVar(value="—"),
        "Color": tk.StringVar(value="—"),
        "Part status": tk.StringVar(value="—"),
        "Color status": tk.StringVar(value="—"),
    }
    detail_row = 13
    for label_text, variable in detail_variables.items():
        ttk.Label(right, text=label_text, font=("Segoe UI", 9, "bold")).grid(
            row=detail_row, column=0, sticky="nw", padx=(2, 6), pady=3
        )
        ttk.Label(
            right,
            textvariable=variable,
            wraplength=245,
            justify="left",
        ).grid(row=detail_row, column=1, sticky="nw", pady=3)
        detail_row += 1

    gallery_status = tk.StringVar(
        value="Select images or folders, then process and review from top to bottom."
    )
    ttk.Label(
        right,
        textvariable=gallery_status,
        wraplength=300,
        foreground="#475569",
    ).grid(row=detail_row, column=0, columnspan=2, sticky="ew", pady=(12, 0))

    filter_value = tk.StringVar(value=image_filter_dropdown.get())
    filter_control = ttk.Combobox(
        right, textvariable=filter_value, state="readonly",
        values=("Needs attention", "All Images", "Suggested Checks", "Part Check", "Color Check", "Ready"),
    )
    filter_control.grid(row=detail_row + 1, column=0, columnspan=2, sticky="ew", pady=6)

    def filter_changed(_event=None):
        selected_for_approval.clear()
        image_filter_dropdown.set(filter_value.get())
        apply_image_filter()
        rebuild_gallery()

    filter_control.bind("<<ComboboxSelected>>", filter_changed)
    selected_for_approval = set()
    selection_variables = []
    group_button = action_button(detail_row + 2, 0, "Approve selected (0)", lambda: approve_selected())
    action_button(detail_row + 2, 1, "Select this page", lambda: select_page())

    def update_group_selection():
        group_button.configure(text=f"Approve selected ({len(selected_for_approval)})")

    def select_page():
        selected_for_approval.clear()
        start = page_state["start"]
        selected_for_approval.update(
            str(Path(path)) for path in visible_image_paths[start:start + page_size]
            if has_identifiers(processed_results.get(str(Path(path))))
        )
        rebuild_gallery()

    def approve_selected():
        if process_button.cget("state") == "disabled" or not selected_for_approval:
            return
        # Approve only explicitly selected, still-visible identities from these thumbnails.
        visible = {str(Path(path)) for path in visible_image_paths}
        if not selected_for_approval <= visible:
            selected_for_approval.clear()
            rebuild_gallery()
            return
        failed = []
        for path in list(selected_for_approval):
            try:
                approved = approve_reviewed_result(processed_results.get(path, {}))
            except (OSError, ValueError) as error:
                failed.append(Path(path).name)
                print(f"Group approval failed for {Path(path).name}: {error}")
                continue
            processed_results[path] = approved
            queue_csv_review(approved)
            selected_for_approval.discard(path)
        update_batch_summary()
        apply_image_filter()
        rebuild_gallery()
        if failed:
            messagebox.showerror("Some pieces were not approved", "Retry these pieces: " + ", ".join(failed), parent=window)

    source_cache = {}
    reference_sources = reference_image_cache
    reference_failures = reference_image_failures
    photo_references = []
    state = {
        "signature": None,
        "rendered_selection": None,
        "rendered_path": None,
    }

    def selected_index():
        selected = image_listbox.curselection()
        return selected[0] if selected else None

    def update_details():
        index = selected_index()
        if index is None or index >= len(visible_image_paths):
            for key, variable in detail_variables.items():
                variable.set("Select an image" if key == "Image" else "—")
            return
        path = Path(visible_image_paths[index])
        result = processed_results.get(str(path))
        detail_variables["Image"].set(path.name)
        if not result:
            for key in detail_variables:
                if key != "Image":
                    detail_variables[key].set("No identification" if key == "Part" else "—")
            return
        detail_variables["Part"].set(f"{result.get('Part Name', 'Unknown')} ({result.get('Part Confidence', 'Unknown')})")
        detail_variables["Part ID"].set(result.get("Part ID", "Unknown"))
        detail_variables["Color"].set(f"{result.get('LEGO Color', 'Unknown')} ({result.get('Color Confidence', 'Unknown')})")
        detail_variables["Part status"].set(
            "Verified" if result.get("Part Review Completed") == "Yes" else
            "Review suggested" if result.get("Part Review Required") == "Yes" else "Saved / unverified"
        )
        detail_variables["Color status"].set(
            "Verified" if result.get("Color Review Completed") == "Yes" else
            "Review suggested" if result.get("Color Review Required") == "Yes" else "Saved / unverified"
        )

    def local_thumbnail(path, size=(290, 145)):
        resolved = str(Path(path))
        if resolved not in source_cache:
            try:
                with Image.open(resolved) as opened:
                    source_cache[resolved] = opened.convert("RGB").copy()
            except (OSError, ValueError):
                source_cache[resolved] = None
        source = source_cache[resolved]
        if source is None:
            return None
        image = source.copy()
        image.thumbnail(size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(image)

    def reference_thumbnail(url, size=(290, 145)):
        source = reference_sources.get(url)
        if source is None:
            request_reference_image(url)
            return None
        image = source.copy()
        image.thumbnail(size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(image)

    def select_gallery_row(index):
        select_review_index(index)
        gallery_canvas.focus_set()
        state["signature"] = None
        rebuild_gallery()

    def normalize_gallery_page():
        total = len(visible_image_paths)
        if total <= 0:
            page_state["start"] = 0
            return
        maximum_start = ((total - 1) // page_size) * page_size
        page_state["start"] = max(
            0,
            min(page_state["start"], maximum_start),
        )

    def change_gallery_page(offset):
        page_state["start"] += int(offset)
        normalize_gallery_page()
        if visible_image_paths:
            # Keep the hidden list selection on the displayed page. Otherwise
            # ensure_selection_page() sees the old row (usually image 1) and
            # immediately sends Next 10 back to the first page.
            select_review_index(page_state["start"])
        gallery_canvas.yview_moveto(0)
        state["signature"] = None
        rebuild_gallery(preserve_scroll=False)

    def ensure_selection_page():
        current = selected_index()
        if current is None:
            return
        start = page_state["start"]
        if current < start or current >= start + page_size:
            page_state["start"] = (current // page_size) * page_size

    def rebuild_gallery(preserve_scroll=True):
        previous_scroll = gallery_canvas.yview()[0] if preserve_scroll else 0.0
        normalize_gallery_page()
        ensure_selection_page()
        photo_references.clear()
        selection_variables.clear()
        selected_for_approval.intersection_update(str(Path(path)) for path in visible_image_paths)
        update_group_selection()
        row_widgets = {}
        for child in gallery_rows.winfo_children():
            child.destroy()
        current = selected_index()
        start = page_state["start"]
        stop = min(start + page_size, len(visible_image_paths))
        page_paths = visible_image_paths[start:stop]

        # Full-resolution source images are the main memory cost. Retain only
        # the current page; moving to another page releases the previous ten.
        active_source_keys = {
            str(Path(reviewed_source_image(Path(raw_path))))
            for raw_path in page_paths
        }
        for cached_path in list(source_cache):
            if cached_path not in active_source_keys:
                source_cache.pop(cached_path, None)

        previous_group = None
        for index, raw_path in enumerate(page_paths, start=start):
            path = Path(raw_path)
            result = processed_results.get(str(path))
            group = review_group(result)
            if group != previous_group:
                label = f"Part {group[0]} · Color {group[1]}" if group[0] else "Unidentified / incomplete pieces"
                ttk.Label(gallery_rows, text=label, font=("Segoe UI", 11, "bold")).pack(fill="x", padx=7, pady=(10, 2))
                previous_group = group
            row_state = get_image_row_state(path)
            background = {
                "done": "#DCFCE7",
                "review": "#FEF3C7",
                "recorded": "#DBEAFE",
                "failed": "#FEE2E2",
            }.get(row_state, "#FFFFFF")
            border = "#038B00" if index == current else "#CBD5E1"
            row = tk.Frame(
                gallery_rows,
                background=background,
                highlightbackground=border,
                highlightcolor=border,
                highlightthickness=3 if index == current else 1,
                cursor="hand2",
            )
            row.pack(fill="x", padx=5, pady=4)
            row_widgets[index] = row
            row.columnconfigure(0, weight=1)
            row.columnconfigure(1, weight=1)
            status_symbol = IMAGE_ROW_STYLES.get(row_state, ("", "", ""))[0]
            heading = tk.Label(
                row,
                text=f"{status_symbol} {path.name}".strip(),
                background=background,
                font=("Segoe UI", 10, "bold"),
                anchor="w",
            )
            heading.grid(row=0, column=0, columnspan=2, sticky="ew", padx=7, pady=(5, 2))

            chosen = tk.BooleanVar(value=str(path) in selected_for_approval)
            selection_variables.append(chosen)

            def toggle_choice(path_key=str(path), variable=chosen):
                if variable.get():
                    selected_for_approval.add(path_key)
                else:
                    selected_for_approval.discard(path_key)
                update_group_selection()

            ttk.Checkbutton(
                row, text="Include in group approval", variable=chosen, command=toggle_choice,
                state="normal" if has_identifiers(result) else "disabled",
            ).grid(row=2, column=0, columnspan=2, sticky="w", padx=7, pady=(0, 5))

            source_photo = local_thumbnail(reviewed_source_image(path))
            source_label = tk.Label(
                row,
                image=source_photo or "",
                text="Scanned image unavailable" if source_photo is None else "",
                background="#FFFFFF",
                compound="center",
            )
            source_label.grid(row=1, column=0, sticky="nsew", padx=(7, 3), pady=(0, 7))
            if source_photo:
                photo_references.append(source_photo)

            reference_url = result.get("Reference Image URL", "") if result else ""
            reference_photo = reference_thumbnail(reference_url) if reference_url else None
            if reference_url in reference_failures:
                reference_message = "Catalog image unavailable"
            elif not result:
                reference_message = "No identification yet"
            else:
                reference_message = "Loading identified image…"
            reference_label = tk.Label(
                row,
                image=reference_photo or "",
                text=reference_message if reference_photo is None else "",
                background="#FFFFFF",
                compound="center",
            )
            reference_label.grid(row=1, column=1, sticky="nsew", padx=(3, 7), pady=(0, 7))
            if reference_photo:
                photo_references.append(reference_photo)

            for widget in (row, heading, source_label, reference_label):
                widget.bind("<Button-1>", lambda _event, item=index: select_gallery_row(item))

        gallery_rows.update_idletasks()
        gallery_canvas.configure(scrollregion=gallery_canvas.bbox("all"))
        selection_changed = current != state["rendered_selection"]
        selected_row = row_widgets.get(current)
        if selection_changed and selected_row is not None:
            # Put keyboard/button navigation where the operator is looking,
            # rather than leaving the newly selected row above or below the
            # visible gallery area.
            content_height = max(gallery_rows.winfo_reqheight(), 1)
            viewport_height = max(gallery_canvas.winfo_height(), 1)
            desired_top = (
                selected_row.winfo_y()
                + selected_row.winfo_height() / 2
                - viewport_height / 2
            )
            gallery_canvas.yview_moveto(
                max(0.0, min(1.0, desired_top / content_height))
            )
        else:
            gallery_canvas.yview_moveto(previous_scroll)
        state["rendered_selection"] = current
        state["rendered_path"] = str(Path(visible_image_paths[current])) if current is not None and current < len(visible_image_paths) else None
        if current is not None and current + 1 < len(visible_image_paths):
            next_result = processed_results.get(str(Path(visible_image_paths[current + 1])), {})
            request_reference_image(next_result.get("Reference Image URL", ""))
        page_text.set(
            f"Images {start + 1 if stop else 0}–{stop} of "
            f"{len(visible_image_paths)}"
        )
        previous_page_button.configure(
            state="normal" if start > 0 else "disabled"
        )
        next_page_button.configure(
            state="normal" if stop < len(visible_image_paths) else "disabled"
        )
        update_details()
        gallery_status.set(
            f"Needs attention: {sum(needs_attention(processed_results.get(str(Path(path)))) for path in selected_image_paths)} "
            f"of {len(selected_image_paths)}. "
            "Select matching thumbnails to approve together. Enter approves one; arrows navigate."
        )

    def signature():
        result_signature = tuple(
            (
                str(path),
                get_image_row_state(path),
                (processed_results.get(str(Path(path))) or {}).get("Part ID", ""),
                (processed_results.get(str(Path(path))) or {}).get("LEGO Color", ""),
                (processed_results.get(str(Path(path))) or {}).get("Reference Image URL", ""),
            )
            for path in visible_image_paths
        )
        return (
            result_signature,
            selected_index(),
            len(reference_sources),
            len(reference_failures),
            page_state["start"],
        )

    def poll_gallery():
        # The gallery replaces the classic toolbar visually, but batch
        # processing still updates the original progress widgets. Mirror that
        # state here so folder batches show live progress in the active UI.
        processing = process_button.cget("state") == "disabled"
        filter_control.configure(state="disabled" if processing else "readonly")
        filter_value.set(image_filter_dropdown.get())
        gallery_progress["maximum"] = max(
            float(batch_progress["maximum"]),
            1,
        )
        gallery_progress["value"] = float(batch_progress["value"])
        gallery_processing_status.configure(
            text=processing_status_label.cget("text"),
            foreground=processing_status_label.cget("foreground"),
        )

        current_signature = signature()
        if current_signature != state["signature"]:
            state["signature"] = current_signature
            rebuild_gallery()
        else:
            update_details()
        if window.winfo_exists():
            window.after(350, poll_gallery)

    def gallery_action(action):
        current = selected_index()
        current_path = str(Path(visible_image_paths[current])) if current is not None and current < len(visible_image_paths) else None
        if action is approve_current_and_next and (not current_path or current_path != state["rendered_path"]):
            rebuild_gallery()
            return
        action()
        window.after(120, update_details)
        state["signature"] = None
        rebuild_gallery()

    def review_shortcut(event):
        if window.focus_get() is None or window.focus_get().winfo_toplevel() != window:
            return
        if window.focus_get().winfo_class() in {"Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox"}:
            return
        if process_button.cget("state") == "disabled":
            return
        if event.keysym in {"Return", "KP_Enter"}:
            gallery_action(approve_current_and_next)
        else:
            gallery_action(lambda: move_review_selection(-1 if event.keysym in {"Left", "Up"} else 1))
        return "break"

    for key in ("Return", "KP_Enter", "Left", "Right", "Up", "Down"):
        window.bind(f"<{key}>", review_shortcut)

    def resize_gallery(_event=None):
        gallery_canvas.itemconfigure(gallery_window_id, width=gallery_canvas.winfo_width())

    def scroll_gallery(event):
        direction = int(-event.delta / 120)
        top, bottom = gallery_canvas.yview()
        start = page_state["start"]
        has_previous = start > 0
        has_next = start + page_size < len(visible_image_paths)

        # Crossing a page boundary discards the previous preview widgets and
        # loads the adjacent ten, keeping gallery memory bounded.
        if direction > 0 and bottom >= 0.999 and has_next:
            change_gallery_page(page_size)
            return "break"
        if direction < 0 and top <= 0.001 and has_previous:
            change_gallery_page(-page_size)
            gallery_canvas.yview_moveto(1)
            return "break"

        gallery_canvas.yview_scroll(direction, "units")
        return "break"

    gallery_canvas.bind("<Configure>", resize_gallery)
    gallery_canvas.bind_all(
        "<MouseWheel>",
        scroll_gallery,
    )
    poll_gallery()


build_gallery_interface()

# Begin the harmless queue poll only after all referenced widgets exist.
window.after(150, check_color_option_queue)
window.after(150, check_reference_image_queue)
window.after(150, check_rescan_queue)
window.protocol("WM_DELETE_WINDOW", quit_application)


# FUNCTION GUIDE: finish_startup
# Purpose: Close the splash screen and reveal the completed workspace.
def finish_startup():
    """Close the splash screen and reveal the completed workspace."""

    splash_progress.stop()
    splash_window.destroy()
    window.deiconify()
    window.lift()
    window.focus_force()


# Leave the completed splash visible briefly so the transition feels
# intentional even on a fast computer.
window.after(900, finish_startup)

window.mainloop()
