"""
Tkinter desktop app for verifying the parts in a LEGO set.

Features:
  - Load official set inventory from Rebrickable.
  - Display mold variations, alternatives, and required counterparts.
  - Edit quantity_have by double-clicking a table cell.
  - Prefill only zero quantities from a reviewed Brickognize CSV export.
  - Review mold-variation substitutes before they count toward the set.
  - Save and resume verification jobs.
  - Compare quantities and export CSV or XML reports.

Run with:
    python tkinter_app.py
"""

from io import BytesIO
from pathlib import Path
import hashlib
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.parse import quote
import webbrowser

import pandas as pd
import requests
from PIL import Image, ImageTk, UnidentifiedImageError

from config import (
    BRICKOGNIZE_RESULTS_DIR,
    JOBS_DIR,
    REPORTS_DIR,
    color_map_csv_path,
    env_path,
    relationships_csv_path,
)
from src.api_rebrickable import (
    RebrickableAPIError,
    SetNotFoundError,
    configure_rebrickable_api_key,
    get_rebrickable_api_key_status,
    get_part_colors_df,
    get_parts_bulk_df,
    get_set_details,
    get_set_parts,
    validate_rebrickable_api_key,
)
from src.api_credentials import clear_api_key, save_api_key
from src.bricklink_mapping import (
    BRICKLINK_MATCH,
    DIRECT_MATCH,
    EXTERNAL_ID_MATCH,
    MANUAL_MATCH,
    attach_bricklink_ids,
    fill_missing_bricklink_ids,
    parse_bricklink_candidates,
    refresh_color_map,
    resolve_rebrickable_part_nums,
    save_manual_part_mapping,
)
from src.app_settings import (
    AUTOMATIC_DATA_UPDATE_DAYS,
    load_app_settings,
    save_app_settings,
)
from src.comparison import (
    build_full_report,
    compare_parts,
    export_report,
    select_wanted_list_rows,
)
from src.relationships import (
    apply_variation_substitutions,
    attach_counterpart_groups,
    attach_variation_relationships,
    attach_variation_surplus_notes,
    find_variation_substitutions,
    get_selected_variation_details,
    load_relationships,
    refresh_relationships_csv,
    attach_print_family_metadata,
)
from src.storage import (
    delete_job,
    list_jobs,
    load_job,
    make_job_id,
    save_job,
)
from src.user_input import (
    combine_entry_csv,
    fill_blank_quantities,
    load_from_csv,
)

class LegoVerificationApp:
    """Main Tkinter application and in-memory verification state."""

    parts_entry_stage = "parts_entry"
    report_review_stage = "report_review"
    complete_stage = "complete"
    import_mode_add = "add"
    import_mode_fill = "fill_blanks"
    import_mode_cancel = "cancel"
    workflow_stages = frozenset({
        parts_entry_stage,
        report_review_stage,
        complete_stage,
    })

    extra_part_metadata_columns = (
        "part_num",
        "color_id",
        "part_name",
        "color_name",
        "image_url",
    )
    wanted_list_remark_columns = (
        "part_num",
        "color_id",
        "wanted_list_remark",
    )
    removal_state_columns = (
        "part_num",
        "color_id",
        "quantity_to_remove",
        "removed",
    )
    removal_statuses = frozenset({"Extra", "Not in set"})
    default_hidden_rebrickable_columns = frozenset({
        "part_num",
        "color_id",
    })

    @classmethod
    def _column_visible_by_default(cls, column):
        return column not in cls.default_hidden_rebrickable_columns

    def __init__(self, root):
        self.root = root
        self.root.title("LEGO Set Verification")
        self.root.geometry("1500x850")
        self.root.minsize(1050, 650)

        # Data shared by the UI actions.
        self.relationships_df = load_relationships(relationships_csv_path)
        self.set_num = None
        self.set_details = None
        self.parts_df = None
        self.user_input_df = None
        self.report_df = None
        self.wanted_list_df = None
        self.job_id = None
        self.job_dirty = False
        self.workflow_stage = self.parts_entry_stage

        # Accepted variation substitutes, and offers the user declined. Both
        # drive row highlighting in the required-parts table.
        self.substitutions = []
        self.declined_substitutions = []

        # Everything needed to reopen the variation review and change an
        # earlier answer: the offers themselves, plus the imported quantities
        # as they were before any substitution moved them.
        self.variation_proposals = None
        self.import_have_df = None
        self.import_history = []
        self.extra_part_metadata_df = pd.DataFrame(
            columns=self.extra_part_metadata_columns
        )
        self.wanted_list_remarks_df = pd.DataFrame(
            columns=self.wanted_list_remark_columns
        )
        self.removal_states_df = pd.DataFrame(
            columns=self.removal_state_columns
        )

        # Maps Treeview row IDs to the part/color key edited by the user.
        self.part_row_keys = {}
        self.report_row_keys = {}
        self.relationship_row_bricklink_parts = {}
        self.saved_job_lookup = {}
        self.saved_jobs = []
        self.start_set_window = None
        self.start_set_var = None
        self.saved_jobs_window = None
        self.saved_jobs_search_var = None
        self.saved_jobs_status_var = None
        self.saved_job_tree = None
        self.saved_job_row_lookup = {}
        self.saved_jobs_delete_button = None
        self.saved_jobs_resume_button = None
        self.removal_window = None
        self.removal_tree = None
        self.removal_status_var = None
        self.removal_row_keys = {}
        self.removal_mark_button = None
        self.removal_pending_button = None
        self.thumbnail_cache = {}
        # Download threads hand finished pictures back through this queue
        # because Tkinter drops calls made from any thread but the main one.
        self.image_queue = queue.Queue()
        self.image_generation = 0
        self.relationship_image_generation = 0
        self.report_image_generation = 0
        self.removal_image_generation = 0
        self.parts_sort_column = None
        self.parts_sort_ascending = True
        self.part_heading_labels = {}
        self.parts_column_vars = {}
        self.parts_search_after_id = None
        self.visible_parts_count = 0
        self.total_parts_count = 0
        self.report_sort_column = None
        self.report_sort_ascending = True
        self.report_heading_labels = {}
        self.report_column_vars = {}
        self.report_search_after_id = None
        self.visible_report_count = 0
        self.total_report_count = 0
        self.busy = False
        self.busy_status = ""
        self.app_settings = load_app_settings()
        self.data_updates_window = None
        self.api_key_window = None
        self.api_key_validation_in_progress = False

        self.set_num_var = tk.StringVar()
        self.saved_job_var = tk.StringVar()
        self.parts_search_var = tk.StringVar()
        self.report_search_var = tk.StringVar()
        self.set_title_var = tk.StringVar(
            value="Load a LEGO set to begin verification."
        )
        self.status_var = tk.StringVar(value="Ready")
        self.automatic_data_updates_var = tk.BooleanVar(
            value=self.app_settings["automatic_data_updates"]
        )
        self.relationship_details_visible_var = tk.BooleanVar(value=False)

        self._configure_styles()
        self._build_ui()
        self._refresh_saved_jobs()
        self._update_command_states()
        self._poll_image_queue()
        self.root.after(200, self._prompt_for_api_key_if_missing)
        self.root.after(500, self._check_automatic_data_updates)

    # =========================================================================
    # UI construction
    # =========================================================================
    def _configure_styles(self):
        style = ttk.Style(self.root)
        style.configure("Heading.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Subheading.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 11, "bold"),
            padding=(18, 10),
        )
        style.configure(
            "Main.TNotebook",
            tabmargins=(0, 4, 0, 0),
        )
        style.configure(
            "Main.TNotebook.Tab",
            font=("Segoe UI", 12, "bold"),
            padding=(22, 10),
            foreground="#6b6b6b",
        )
        style.map(
            "Main.TNotebook.Tab",
            foreground=[
                ("selected", "#000000"),
                ("active", "#4a4a4a"),
            ],
        )
        style.configure("Treeview", rowheight=30)
        style.configure("Parts.Treeview", rowheight=72)

    def _bind_fast_horizontal_scroll(self, tree):
        """Make Shift+wheel move a table four times faster than Tk's default."""
        tree.bind(
            "<Shift-MouseWheel>",
            lambda event: self._scroll_table_horizontally(tree, event),
        )
        # Tk reports wheel buttons instead of a delta on some platforms.
        tree.bind(
            "<Shift-Button-4>",
            lambda _event: self._scroll_table_horizontally(tree, units=-4),
        )
        tree.bind(
            "<Shift-Button-5>",
            lambda _event: self._scroll_table_horizontally(tree, units=4),
        )

    @staticmethod
    def _scroll_table_horizontally(tree, event=None, units=None):
        """Scroll horizontally and suppress the slower Treeview class binding."""
        if units is None:
            delta = int(getattr(event, "delta", 0) or 0)
            if not delta:
                return "break"

            notches = int(delta / 120)
            if notches == 0:
                notches = 1 if delta > 0 else -1
            units = -notches * 4

        tree.xview_scroll(units, "units")
        return "break"

    def _build_ui(self):
        self._build_menu_bar()

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        heading = ttk.Frame(outer)
        heading.pack(fill="x", pady=(0, 8))
        heading.columnconfigure(0, weight=1)

        ttk.Label(
            heading,
            textvariable=self.set_title_var,
            style="Heading.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=(0, 18))

        ttk.Label(
            heading,
            text="Use File > Start New Set to begin.",
        ).grid(row=1, column=0, sticky="w", padx=(0, 18), pady=(5, 0))

        self.compare_parts_button = ttk.Button(
            heading,
            text="Compare Parts",
            style="Primary.TButton",
            command=self.compare_parts,
        )
        self.compare_parts_button.grid(
            row=0,
            column=1,
            rowspan=2,
            sticky="e",
        )

        self.content_paned = ttk.Panedwindow(outer, orient="vertical")
        self.content_paned.pack(fill="both", expand=True)

        self.main_tables_frame = ttk.Frame(self.content_paned)
        self.relationship_details_frame = ttk.LabelFrame(
            self.content_paned,
            text="Variation/Alternative Details",
            padding=6,
        )
        self.content_paned.add(self.main_tables_frame, weight=3)

        self._build_main_table_tabs(self.main_tables_frame)
        self._build_relationship_details(self.relationship_details_frame)

        ttk.Separator(outer).pack(fill="x", pady=(8, 5))
        ttk.Label(outer, textvariable=self.status_var).pack(fill="x")

    def _build_menu_bar(self):
        self.menu_bar = tk.Menu(self.root, tearoff=False)
        self.file_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.file_menu_indices = {}

        self.file_menu.add_command(
            label="Start New Set...",
            accelerator="Ctrl+N",
            command=self.open_start_new_set_dialog,
        )
        self.file_menu_indices["start"] = self.file_menu.index("end")

        self.resume_jobs_menu = tk.Menu(self.file_menu, tearoff=False)
        self.file_menu.add_cascade(
            label="Resume Saved Job",
            menu=self.resume_jobs_menu,
        )
        self.file_menu_indices["resume"] = self.file_menu.index("end")
        self.file_menu.add_separator()

        self.file_menu.add_command(
            label="Prefill Brickognize CSV...",
            accelerator="Ctrl+I",
            command=self.prefill_csv,
        )
        self.file_menu_indices["prefill"] = self.file_menu.index("end")
        self.file_menu.add_command(
            label="Review BrickLink Mappings...",
            command=self.review_bricklink_mappings,
        )
        self.file_menu_indices["bricklink_mappings"] = self.file_menu.index(
            "end"
        )
        self.file_menu.add_command(
            label="Save Progress",
            accelerator="Ctrl+S",
            command=self.save_progress,
        )
        self.file_menu_indices["save"] = self.file_menu.index("end")
        self.file_menu.add_command(
            label="Export Report...",
            accelerator="Ctrl+E",
            command=self.export_current_report,
        )
        self.file_menu_indices["export"] = self.file_menu.index("end")
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self._request_exit)
        self.menu_bar.add_cascade(label="File", menu=self.file_menu)

        self.view_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.view_menu.add_checkbutton(
            label="Variation/Alternative Details",
            variable=self.relationship_details_visible_var,
            command=self._toggle_relationship_details,
        )
        self.menu_bar.add_cascade(label="View", menu=self.view_menu)

        self.settings_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.settings_menu.add_command(
            label="Rebrickable API Key...",
            command=self.open_api_key_settings,
        )
        self.settings_menu.add_separator()
        self.settings_menu.add_command(
            label="Data updates...",
            command=self.open_data_updates,
        )
        self.menu_bar.add_cascade(
            label="Settings",
            menu=self.settings_menu,
        )
        self.root.configure(menu=self.menu_bar)

        shortcuts = {
            "<Control-n>": ("start", self.open_start_new_set_dialog),
            "<Control-i>": ("prefill", self.prefill_csv),
            "<Control-s>": ("save", self.save_progress),
            "<Control-e>": ("export", self.export_current_report),
        }
        for sequence, (command_name, command) in shortcuts.items():
            self.root.bind_all(
                sequence,
                lambda _event, name=command_name, action=command: (
                    self._invoke_shortcut(name, action)
                ),
            )
        self.root.protocol("WM_DELETE_WINDOW", self._request_exit)

    def _relationship_details_is_shown(self):
        if not hasattr(self, "content_paned"):
            return False
        detail_path = str(self.relationship_details_frame)
        return detail_path in {
            str(pane_path)
            for pane_path in self.content_paned.panes()
        }

    def _toggle_relationship_details(self):
        """Show or hide the optional lower pane without destroying its state."""
        should_show = bool(self.relationship_details_visible_var.get())
        is_shown = self._relationship_details_is_shown()

        if should_show and not is_shown:
            self.content_paned.add(
                self.relationship_details_frame,
                weight=2,
            )
            # The new child is still one pixel tall during the first idle
            # layout pass. Position the sash on the next event-loop cycle,
            # after Tk has mapped both panes and can honor the requested split.
            self.root.after(25, self._position_relationship_details_pane)
        elif not should_show and is_shown:
            self.content_paned.forget(self.relationship_details_frame)

    def _position_relationship_details_pane(self):
        """Give the main verification tables most of the available height."""
        if not self._relationship_details_is_shown():
            return

        available_height = self.content_paned.winfo_height()
        if available_height <= 1:
            return

        main_table_height = min(
            int(available_height * 0.62),
            max(1, available_height - 180),
        )
        self.content_paned.sashpos(0, main_table_height)

    def _prompt_for_api_key_if_missing(self):
        """Open first-run setup only when no usable key is configured."""
        if (
            self.root.state() != "withdrawn"
            and not get_rebrickable_api_key_status()["configured"]
        ):
            self.open_api_key_settings(first_run=True)

    def open_api_key_settings(self, first_run=False):
        """Open the masked API-key setup and replacement dialog."""
        if (
            self.api_key_window is not None
            and self.api_key_window.winfo_exists()
        ):
            self.api_key_window.lift()
            self.api_key_window.focus_set()
            return

        window = tk.Toplevel(self.root)
        self.api_key_window = window
        window.title(
            "Set up Rebrickable" if first_run else "Rebrickable API Key"
        )
        window.transient(self.root)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._close_api_key_settings)

        content = ttk.Frame(window, padding=16)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)

        ttk.Label(
            content,
            text=(
                "Welcome — connect Rebrickable"
                if first_run
                else "Rebrickable API access"
            ),
            style="Subheading.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            content,
            text=(
                "The app uses your personal Rebrickable API key to load LEGO "
                "sets, part details, colors, and images. Paste the key below; "
                "it will be stored only for this Windows user."
            ),
            wraplength=520,
            justify="left",
        ).grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(5, 12),
        )

        self.api_key_status_var = tk.StringVar()
        ttk.Label(
            content,
            textvariable=self.api_key_status_var,
            wraplength=520,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(content, text="API key:").grid(row=3, column=0, sticky="w")
        self.api_key_entry_var = tk.StringVar()
        self.api_key_entry = ttk.Entry(
            content,
            textvariable=self.api_key_entry_var,
            width=48,
            show="*",
        )
        self.api_key_entry.grid(row=4, column=0, sticky="ew", pady=(4, 6))
        self.api_key_entry.bind(
            "<Return>",
            lambda _event: self._test_and_save_api_key(),
        )
        ttk.Button(
            content,
            text="Paste",
            command=self._paste_api_key,
        ).grid(row=4, column=1, padx=(8, 0), pady=(4, 6))

        self.show_api_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            content,
            text="Show key",
            variable=self.show_api_key_var,
            command=self._toggle_api_key_visibility,
        ).grid(row=5, column=0, sticky="w")
        ttk.Button(
            content,
            text="Get an API key",
            command=lambda: webbrowser.open("https://rebrickable.com/api/"),
        ).grid(row=5, column=1, padx=(8, 0), sticky="e")

        ttk.Label(
            content,
            text=f"Saved location: {env_path}",
            foreground="#666666",
            wraplength=520,
        ).grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(10, 12),
        )

        button_frame = ttk.Frame(content)
        button_frame.grid(row=7, column=0, columnspan=3, sticky="ew")
        button_frame.columnconfigure(1, weight=1)
        self.remove_api_key_button = ttk.Button(
            button_frame,
            text="Remove Saved Key",
            command=self._remove_api_key,
        )
        self.remove_api_key_button.grid(row=0, column=0, sticky="w")
        self.api_key_save_button = ttk.Button(
            button_frame,
            text="Test and Save",
            command=self._test_and_save_api_key,
        )
        self.api_key_save_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(
            button_frame,
            text="Not Now" if first_run else "Close",
            command=self._close_api_key_settings,
        ).grid(row=0, column=3, padx=(8, 0))

        self._refresh_api_key_dialog()
        window.update_idletasks()
        window.minsize(window.winfo_reqwidth(), window.winfo_reqheight())
        if first_run:
            window.grab_set()
        self.api_key_entry.focus_set()

    def _close_api_key_settings(self):
        if self.api_key_validation_in_progress:
            messagebox.showinfo(
                "API key test in progress",
                "Please wait for the Rebrickable API key test to finish.",
                parent=self.api_key_window or self.root,
            )
            return
        if (
            self.api_key_window is not None
            and self.api_key_window.winfo_exists()
        ):
            self.api_key_window.destroy()
        self.api_key_window = None

    def _refresh_api_key_dialog(self, message=None):
        if (
            self.api_key_window is None
            or not self.api_key_window.winfo_exists()
        ):
            return

        status = get_rebrickable_api_key_status()
        source_labels = {
            "user_file": "Configured for this Windows user.",
            "environment": "Configured by an environment variable.",
            "legacy_file": "Configured from the project .env file.",
            "missing": "No API key is configured.",
        }
        self.api_key_status_var.set(
            message or source_labels.get(status["source"], "API key status unknown.")
        )
        state = (
            "disabled"
            if self.api_key_validation_in_progress
            else "normal"
        )
        self.api_key_save_button.configure(state=state)
        self.remove_api_key_button.configure(
            state=(
                "normal"
                if status["configured"] and not self.api_key_validation_in_progress
                else "disabled"
            )
        )

    def _toggle_api_key_visibility(self):
        self.api_key_entry.configure(
            show="" if self.show_api_key_var.get() else "*"
        )

    def _paste_api_key(self):
        try:
            clipboard_value = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showinfo(
                "Clipboard is empty",
                "Copy the Rebrickable API key, then try Paste again.",
                parent=self.api_key_window or self.root,
            )
            return
        self.api_key_entry_var.set(str(clipboard_value).strip())

    def _test_and_save_api_key(self):
        if self.api_key_validation_in_progress:
            return

        api_key = self.api_key_entry_var.get().strip()
        if not api_key:
            messagebox.showwarning(
                "API key required",
                "Paste a Rebrickable API key before testing it.",
                parent=self.api_key_window or self.root,
            )
            return

        self.api_key_validation_in_progress = True
        self._refresh_api_key_dialog("Testing the API key with Rebrickable...")

        def run():
            try:
                validate_rebrickable_api_key(api_key)
            except Exception as exc:
                self.root.after(
                    0,
                    lambda error=exc: self._finish_api_key_validation(
                        api_key,
                        error,
                    ),
                )
            else:
                self.root.after(
                    0,
                    lambda: self._finish_api_key_validation(api_key, None),
                )

        threading.Thread(target=run, daemon=True).start()

    def _finish_api_key_validation(self, api_key, error):
        self.api_key_validation_in_progress = False
        if error is not None:
            self._refresh_api_key_dialog("The API key was not saved.")
            messagebox.showerror(
                "Could not verify API key",
                str(error),
                parent=self.api_key_window or self.root,
            )
            return

        try:
            save_api_key(api_key)
        except OSError as exc:
            self._refresh_api_key_dialog("The API key was valid but could not be saved.")
            messagebox.showerror(
                "Could not save API key",
                str(exc),
                parent=self.api_key_window or self.root,
            )
            return

        configure_rebrickable_api_key(api_key, source="user_file")
        self.api_key_entry_var.set("")
        self._refresh_api_key_dialog(
            "API key verified, saved, and ready to use."
        )
        self.status_var.set("Rebrickable API key updated.")
        self.root.after(500, self._check_automatic_data_updates)
        messagebox.showinfo(
            "API key saved",
            "The Rebrickable API key is active. You do not need to restart.",
            parent=self.api_key_window or self.root,
        )

    def _remove_api_key(self):
        if not messagebox.askyesno(
            "Remove API key?",
            (
                "Remove the saved Rebrickable API key from this Windows user? "
                "Online set and part lookups will stop until another key is saved."
            ),
            parent=self.api_key_window or self.root,
        ):
            return

        try:
            clear_api_key()
        except OSError as exc:
            messagebox.showerror(
                "Could not remove API key",
                str(exc),
                parent=self.api_key_window or self.root,
            )
            return

        configure_rebrickable_api_key("", source="user_file")
        self.api_key_entry_var.set("")
        self._refresh_api_key_dialog("API key removed.")
        self.status_var.set("Rebrickable API key removed.")

    def open_data_updates(self):
        if (
            self.data_updates_window is not None
            and self.data_updates_window.winfo_exists()
        ):
            self.data_updates_window.lift()
            self.data_updates_window.focus_set()
            return

        window = tk.Toplevel(self.root)
        self.data_updates_window = window
        window.title("Data updates")
        window.transient(self.root)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._close_data_updates)

        content = ttk.Frame(window, padding=14)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)

        ttk.Label(
            content,
            text="Reference data",
            style="Subheading.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            content,
            text=(
                "Updates are validated before replacing the current files. "
                "The previous files are kept as backups."
            ),
            wraplength=520,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 12))

        self.relationship_update_status_var = tk.StringVar()
        self.color_update_status_var = tk.StringVar()

        relationship_frame = ttk.LabelFrame(
            content,
            text="Part relationships",
            padding=10,
        )
        relationship_frame.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 8),
        )
        relationship_frame.columnconfigure(0, weight=1)
        ttk.Label(
            relationship_frame,
            textvariable=self.relationship_update_status_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.refresh_relationships_button = ttk.Button(
            relationship_frame,
            text="Refresh now",
            command=lambda: self._start_reference_data_refresh(
                ("relationships",),
                manual=True,
            ),
        )
        self.refresh_relationships_button.grid(row=0, column=1)

        color_frame = ttk.LabelFrame(
            content,
            text="Color map",
            padding=10,
        )
        color_frame.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 12),
        )
        color_frame.columnconfigure(0, weight=1)
        ttk.Label(
            color_frame,
            textvariable=self.color_update_status_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.refresh_color_map_button = ttk.Button(
            color_frame,
            text="Refresh now",
            command=lambda: self._start_reference_data_refresh(
                ("colors",),
                manual=True,
            ),
        )
        self.refresh_color_map_button.grid(row=0, column=1)

        ttk.Checkbutton(
            content,
            text=(
                "Automatically check reference data weekly when the app "
                "starts"
            ),
            variable=self.automatic_data_updates_var,
            command=self._save_automatic_data_update_setting,
        ).grid(row=4, column=0, columnspan=2, sticky="w")

        self.data_update_availability_var = tk.StringVar()
        ttk.Label(
            content,
            textvariable=self.data_update_availability_var,
            foreground="#7a4b00",
            wraplength=520,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 12))

        ttk.Button(
            content,
            text="Close",
            command=self._close_data_updates,
        ).grid(row=6, column=1, sticky="e")

        self._refresh_data_update_dialog()
        window.update_idletasks()
        window.minsize(window.winfo_reqwidth(), window.winfo_reqheight())

    def _close_data_updates(self):
        if (
            self.data_updates_window is not None
            and self.data_updates_window.winfo_exists()
        ):
            self.data_updates_window.destroy()
        self.data_updates_window = None

    @staticmethod
    def _reference_data_summary(path):
        data_path = Path(path)
        if not data_path.exists():
            return "Not downloaded"

        try:
            row_count = len(pd.read_csv(data_path))
        except (OSError, ValueError, UnicodeDecodeError):
            return "Local file could not be read"

        updated = time.strftime(
            "%Y-%m-%d %H:%M",
            time.localtime(data_path.stat().st_mtime),
        )
        return f"Last updated {updated} | {row_count:,} rows"

    def _refresh_data_update_dialog(self):
        if (
            self.data_updates_window is None
            or not self.data_updates_window.winfo_exists()
        ):
            return

        self.relationship_update_status_var.set(
            self._reference_data_summary(relationships_csv_path)
        )
        self.color_update_status_var.set(
            self._reference_data_summary(color_map_csv_path)
        )

        unavailable = self.parts_df is not None or self.busy
        button_state = "disabled" if unavailable else "normal"
        self.refresh_relationships_button.configure(state=button_state)
        self.refresh_color_map_button.configure(state=button_state)

        if self.parts_df is not None:
            message = (
                "Reference files cannot be replaced while a set is loaded. "
                "Restart the app and update before loading or resuming a job."
            )
        elif self.busy:
            message = "Another operation is currently running."
        else:
            message = ""
        self.data_update_availability_var.set(message)

    def _save_automatic_data_update_setting(self):
        self.app_settings["automatic_data_updates"] = bool(
            self.automatic_data_updates_var.get()
        )
        try:
            save_app_settings(self.app_settings)
        except OSError as exc:
            messagebox.showerror(
                "Could not save settings",
                str(exc),
                parent=self.data_updates_window or self.root,
            )

    @staticmethod
    def _reference_data_is_stale(path, now=None):
        data_path = Path(path)
        if not data_path.exists():
            return True
        if now is None:
            now = time.time()
        maximum_age = AUTOMATIC_DATA_UPDATE_DAYS * 24 * 60 * 60
        return now - data_path.stat().st_mtime >= maximum_age

    def _check_automatic_data_updates(self):
        if (
            not self.automatic_data_updates_var.get()
            or not get_rebrickable_api_key_status()["configured"]
            or self.parts_df is not None
            or self.busy
        ):
            return

        resources = []
        if self._reference_data_is_stale(relationships_csv_path):
            resources.append("relationships")
        if self._reference_data_is_stale(color_map_csv_path):
            resources.append("colors")

        if resources:
            self._start_reference_data_refresh(
                tuple(resources),
                manual=False,
            )

    def _start_reference_data_refresh(self, resources, manual):
        if self.parts_df is not None:
            messagebox.showinfo(
                "Update deferred",
                "Restart the app and update before loading or resuming a job.",
                parent=self.data_updates_window or self.root,
            )
            return
        if self.busy:
            messagebox.showinfo(
                "Please wait",
                "Another operation is still running.",
                parent=self.data_updates_window or self.root,
            )
            return

        labels = {
            "relationships": "part relationships",
            "colors": "color map",
        }
        description = " and ".join(labels[item] for item in resources)
        self._run_background(
            status=f"Updating {description}...",
            work=lambda: self._refresh_reference_data(resources),
            on_success=lambda result: self._finish_reference_data_refresh(
                result,
                manual=manual,
            ),
        )
        self._refresh_data_update_dialog()

    @staticmethod
    def _refresh_reference_data(resources):
        result = {
            "relationships_df": None,
            "color_map_df": None,
            "errors": [],
        }

        if "relationships" in resources:
            try:
                result["relationships_df"] = refresh_relationships_csv()
            except Exception as exc:
                result["errors"].append(f"Part relationships: {exc}")

        if "colors" in resources:
            try:
                color_map_df = refresh_color_map()
                if color_map_df.empty:
                    raise ValueError("Rebrickable returned no color mappings.")
                result["color_map_df"] = color_map_df
            except Exception as exc:
                result["errors"].append(f"Color map: {exc}")

        return result

    def _finish_reference_data_refresh(self, result, manual):
        messages = []
        relationships_df = result.get("relationships_df")
        if relationships_df is not None:
            self.relationships_df = relationships_df
            messages.append(
                f"Updated {len(relationships_df):,} part relationships."
            )

        color_map_df = result.get("color_map_df")
        if color_map_df is not None:
            messages.append(f"Updated {len(color_map_df):,} color mappings.")

        errors = result.get("errors") or []
        self._refresh_data_update_dialog()

        if messages:
            self.status_var.set(" ".join(messages))
        elif errors:
            self.status_var.set("Reference data update failed.")

        parent = self.data_updates_window or self.root
        if errors:
            detail = "\n".join(errors)
            if messages:
                detail = f"{' '.join(messages)}\n\n{detail}"
            messagebox.showwarning(
                "Reference data update issue",
                detail,
                parent=parent,
            )
        elif manual:
            messagebox.showinfo(
                "Reference data updated",
                "\n".join(messages),
                parent=parent,
            )

    def _command_is_available(self, command_name):
        """Return whether a File-menu action is valid for the current state."""
        if self.busy:
            return False
        if command_name == "start":
            return True
        if command_name == "resume":
            return bool(self.saved_jobs)
        if command_name == "prefill":
            return (
                self.parts_df is not None
                and self.workflow_stage != self.complete_stage
            )
        if command_name == "bricklink_mappings":
            return (
                self.parts_df is not None
                and self.workflow_stage != self.complete_stage
                and bool(self._ambiguous_bricklink_mappings(self.parts_df))
            )
        if command_name == "save":
            return self.parts_df is not None
        if command_name == "export":
            return self.report_df is not None
        return False

    def _update_command_states(self):
        """Keep menu commands and the primary button in sync with app state."""
        if not hasattr(self, "file_menu"):
            return

        for command_name, menu_index in self.file_menu_indices.items():
            state = (
                "normal"
                if self._command_is_available(command_name)
                else "disabled"
            )
            self.file_menu.entryconfigure(menu_index, state=state)

        compare_state = (
            "normal"
            if (
                self.parts_df is not None
                and self.workflow_stage == self.parts_entry_stage
                and not self.busy
            )
            else "disabled"
        )
        self.compare_parts_button.configure(state=compare_state)

        if hasattr(self, "finish_verification_button"):
            is_complete = self.workflow_stage == self.complete_stage
            self.finish_verification_button.configure(
                text=(
                    "Reopen Verification"
                    if is_complete
                    else "Finish Verification"
                ),
                state=(
                    "normal"
                    if (
                        self.report_df is not None
                        and self.workflow_stage in {
                            self.report_review_stage,
                            self.complete_stage,
                        }
                        and not self.busy
                    )
                    else "disabled"
                ),
            )

        if hasattr(self, "removal_checklist_button"):
            removal_count = len(self._removal_rows(self.report_df))
            pending_count = self._pending_removal_count()
            button_text = "Removal Checklist"
            if removal_count:
                button_text += f" ({pending_count} pending)"
            self.removal_checklist_button.configure(
                text=button_text,
                state=(
                    "normal"
                    if (
                        removal_count
                        and self.workflow_stage in {
                            self.report_review_stage,
                            self.complete_stage,
                        }
                        and not self.busy
                    )
                    else "disabled"
                ),
            )

        self._update_bulk_remark_button()

        self._update_bricklink_catalog_button()

        if (
            self.saved_jobs_window is not None
            and self.saved_jobs_window.winfo_exists()
        ):
            selected_job = self._selected_saved_job()
            self.saved_jobs_resume_button.configure(
                state=(
                    "normal"
                    if selected_job is not None and not self.busy
                    else "disabled"
                )
            )
            self.saved_jobs_delete_button.configure(
                state=(
                    "normal"
                    if (
                        selected_job is not None
                        and selected_job.get("job_id") != self.job_id
                        and not self.busy
                    )
                    else "disabled"
                )
            )

    def _invoke_shortcut(self, command_name, command):
        """Run an accelerator only when its corresponding menu item is active."""
        if self._command_is_available(command_name):
            command()
        return "break"

    def open_start_new_set_dialog(self):
        if self.busy:
            return
        if (
            self.start_set_window is not None
            and self.start_set_window.winfo_exists()
        ):
            self.start_set_window.lift()
            self.start_set_window.focus_set()
            return

        window = tk.Toplevel(self.root)
        self.start_set_window = window
        self.start_set_var = tk.StringVar()
        window.title("Start New Set")
        window.transient(self.root)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._close_start_set_dialog)

        content = ttk.Frame(window, padding=16)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)

        ttk.Label(
            content,
            text="Enter the Rebrickable LEGO set number.",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(content, text="Set number:").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(12, 4),
        )
        entry = ttk.Entry(
            content,
            textvariable=self.start_set_var,
            width=28,
        )
        entry.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 16),
        )

        ttk.Button(
            content,
            text="Cancel",
            command=self._close_start_set_dialog,
        ).grid(row=3, column=0, sticky="e", padx=(0, 8))
        ttk.Button(
            content,
            text="Load Set",
            command=self._submit_start_new_set,
        ).grid(row=3, column=1, sticky="e")

        entry.bind("<Return>", lambda _event: self._submit_start_new_set())
        window.bind("<Escape>", lambda _event: self._close_start_set_dialog())
        window.grab_set()
        entry.focus_set()
        window.update_idletasks()
        window.minsize(window.winfo_reqwidth(), window.winfo_reqheight())

    def _close_start_set_dialog(self):
        if (
            self.start_set_window is not None
            and self.start_set_window.winfo_exists()
        ):
            self.start_set_window.destroy()
        self.start_set_window = None
        self.start_set_var = None

    def _submit_start_new_set(self):
        set_num = (
            self.start_set_var.get().strip()
            if self.start_set_var is not None
            else ""
        )
        if not set_num:
            messagebox.showwarning(
                "Set number required",
                "Enter a LEGO set number first.",
                parent=self.start_set_window or self.root,
            )
            return

        if not self._confirm_leave_current_job(
            parent=self.start_set_window or self.root
        ):
            return

        self._close_start_set_dialog()
        self.start_new_job(set_num)

    def open_saved_jobs_dialog(self):
        if (
            self.saved_jobs_window is not None
            and self.saved_jobs_window.winfo_exists()
        ):
            self.saved_jobs_window.lift()
            self.saved_jobs_window.focus_set()
            return

        window = tk.Toplevel(self.root)
        self.saved_jobs_window = window
        self.saved_jobs_search_var = tk.StringVar()
        self.saved_jobs_status_var = tk.StringVar()
        window.title("Saved Jobs")
        window.transient(self.root)
        window.geometry("780x430")
        window.minsize(640, 320)
        window.protocol("WM_DELETE_WINDOW", self._close_saved_jobs_dialog)

        content = ttk.Frame(window, padding=12)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)

        ttk.Label(content, text="Search saved jobs:").grid(
            row=0,
            column=0,
            sticky="w",
        )
        search_entry = ttk.Entry(
            content,
            textvariable=self.saved_jobs_search_var,
        )
        search_entry.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        search_entry.bind(
            "<KeyRelease>",
            lambda _event: self._render_saved_jobs_dialog(),
        )

        tree_frame = ttk.Frame(content)
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.saved_job_tree = ttk.Treeview(
            tree_frame,
            columns=("set_num", "status", "updated_at"),
            show="headings",
            selectmode="browse",
        )
        self.saved_job_tree.heading("set_num", text="Set")
        self.saved_job_tree.heading("status", text="Status")
        self.saved_job_tree.heading("updated_at", text="Last updated")
        self.saved_job_tree.column("set_num", width=140, anchor="w")
        self.saved_job_tree.column("status", width=140, anchor="w")
        self.saved_job_tree.column("updated_at", width=300, anchor="w")
        scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.saved_job_tree.yview,
        )
        self.saved_job_tree.configure(yscrollcommand=scrollbar.set)
        self.saved_job_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.saved_job_tree.bind(
            "<<TreeviewSelect>>",
            lambda _event: self._update_command_states(),
        )
        self.saved_job_tree.bind(
            "<Double-1>",
            lambda _event: self._resume_job_from_saved_jobs_dialog(),
        )

        footer = ttk.Frame(content)
        footer.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            textvariable=self.saved_jobs_status_var,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            footer,
            text="Cancel",
            command=self._close_saved_jobs_dialog,
        ).grid(row=0, column=2, padx=(8, 8))
        self.saved_jobs_delete_button = ttk.Button(
            footer,
            text="Delete Selected Job...",
            command=self._delete_job_from_saved_jobs_dialog,
        )
        self.saved_jobs_delete_button.grid(
            row=0,
            column=1,
            padx=(8, 0),
        )
        self.saved_jobs_resume_button = ttk.Button(
            footer,
            text="Resume Selected Job",
            command=self._resume_job_from_saved_jobs_dialog,
        )
        self.saved_jobs_resume_button.grid(row=0, column=3)

        window.bind("<Escape>", lambda _event: self._close_saved_jobs_dialog())
        window.grab_set()
        self._render_saved_jobs_dialog()
        search_entry.focus_set()

    def _close_saved_jobs_dialog(self):
        if (
            self.saved_jobs_window is not None
            and self.saved_jobs_window.winfo_exists()
        ):
            self.saved_jobs_window.destroy()
        self.saved_jobs_window = None
        self.saved_jobs_search_var = None
        self.saved_jobs_status_var = None
        self.saved_job_tree = None
        self.saved_job_row_lookup = {}
        self.saved_jobs_delete_button = None
        self.saved_jobs_resume_button = None

    def _render_saved_jobs_dialog(self):
        if (
            self.saved_jobs_window is None
            or not self.saved_jobs_window.winfo_exists()
        ):
            return

        query = self.saved_jobs_search_var.get().strip().casefold()
        matching_jobs = [
            job
            for job in self.saved_jobs
            if not query
            or query in self._saved_job_search_text(job).casefold()
        ]
        self._clear_tree(self.saved_job_tree)
        self.saved_job_row_lookup = {}

        for position, job in enumerate(matching_jobs):
            item_id = f"saved_job_{position}"
            self.saved_job_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    job.get("set_num", "?"),
                    self._display_job_status(job.get("status")),
                    self._display_job_timestamp(job.get("updated_at")),
                ),
            )
            self.saved_job_row_lookup[item_id] = job.get("job_id")

        if matching_jobs:
            first_item = self.saved_job_tree.get_children()[0]
            self.saved_job_tree.selection_set(first_item)
            self.saved_job_tree.focus(first_item)
        self.saved_jobs_status_var.set(
            f"{len(matching_jobs)} of {len(self.saved_jobs)} saved job(s)"
        )
        self._update_command_states()

    def _selected_saved_job(self):
        """Return metadata for the single job selected in the dialog."""
        if self.saved_job_tree is None:
            return None

        selection = self.saved_job_tree.selection()
        if not selection:
            return None

        selected_job_id = self.saved_job_row_lookup.get(selection[0])
        if not selected_job_id:
            return None

        return next(
            (
                job
                for job in self.saved_jobs
                if job.get("job_id") == selected_job_id
            ),
            None,
        )

    def _delete_job_from_saved_jobs_dialog(self):
        """Confirm and permanently delete a non-active saved job."""
        if self.busy:
            return

        selected_job = self._selected_saved_job()
        if selected_job is None:
            return

        job_id = selected_job.get("job_id")
        parent = self.saved_jobs_window or self.root
        if job_id == self.job_id:
            messagebox.showinfo(
                "Saved job currently open",
                (
                    "This saved job is currently open. Start or resume a "
                    "different job before deleting it."
                ),
                parent=parent,
            )
            return

        set_num = selected_job.get("set_num", "?")
        status = self._display_job_status(selected_job.get("status"))
        updated_at = self._display_job_timestamp(
            selected_job.get("updated_at")
        )
        confirmed = messagebox.askyesno(
            "Delete saved job?",
            (
                "Permanently delete this saved job?\n\n"
                f"Set: {set_num}\n"
                f"Status: {status}\n"
                f"Last updated: {updated_at}\n\n"
                "This action cannot be undone."
            ),
            icon="warning",
            parent=parent,
        )
        if not confirmed:
            return

        try:
            delete_job(JOBS_DIR, job_id)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Could not delete saved job",
                str(exc),
                parent=parent,
            )
            return

        self._refresh_saved_jobs()
        self.status_var.set(f"Deleted saved job for set {set_num}.")

    def _resume_job_from_saved_jobs_dialog(self):
        if self.busy or self.saved_job_tree is None:
            return
        selection = self.saved_job_tree.selection()
        if not selection:
            return
        job_id = self.saved_job_row_lookup.get(selection[0])
        if not job_id:
            return
        if not self._confirm_leave_current_job(
            parent=self.saved_jobs_window or self.root
        ):
            return
        self._close_saved_jobs_dialog()
        self.resume_selected_job(job_id, confirm_leave=False)

    def _build_main_table_tabs(self, parent):
        self.main_table_notebook = ttk.Notebook(
            parent,
            style="Main.TNotebook",
        )
        self.main_table_notebook.pack(fill="both", expand=True)

        self.parts_tab = ttk.Frame(self.main_table_notebook, padding=6)
        self.report_tab = ttk.Frame(self.main_table_notebook, padding=6)
        self.main_table_notebook.add(
            self.parts_tab,
            text="Required Parts",
        )
        self.main_table_notebook.add(
            self.report_tab,
            text="Verification Report",
        )
        self.main_table_notebook.bind(
            "<<NotebookTabChanged>>",
            self._update_main_table_tab_labels,
        )
        self._update_main_table_tab_labels()

        self._build_parts_table(self.parts_tab)
        self._build_report_table(self.report_tab)

    def _update_main_table_tab_labels(self, _event=None):
        """Make the active verification table unmistakable across Tk themes."""
        selected_tab = self.main_table_notebook.select()
        tab_labels = (
            (self.parts_tab, "Required Parts"),
            (self.report_tab, "Verification Report"),
        )
        for tab, label in tab_labels:
            is_selected = str(tab) == str(selected_tab)
            tab_text = (
                f"▶  {label}"
                if is_selected
                else label
            )
            self.main_table_notebook.tab(tab, text=tab_text)

    def _build_parts_table(self, parent):
        # BrickLink columns lead because they are what the client uploads;
        # the Rebrickable values stay visible alongside them.
        columns = (
            "bl_part_num",
            "bl_color_id",
            "part_num",
            "part_name",
            "color_id",
            "color_name",
            "required",
            "have",
            "variation",
            "counterpart",
            "mold_variations",
            "alternatives",
        )
        # Keep Rebrickable identifiers available in the Columns menu, but hide
        # them initially because BrickLink is the operator-facing catalog.
        self.parts_tree = ttk.Treeview(
            parent,
            columns=columns,
            displaycolumns=tuple(
                column
                for column in columns
                if self._column_visible_by_default(column)
            ),
            show="tree headings",
            selectmode="browse",
            style="Parts.Treeview",
        )
        self.parts_tree.heading("#0", text="Image")
        self.parts_tree.column(
            "#0",
            width=82,
            minwidth=82,
            stretch=False,
            anchor="center",
        )

        self.part_heading_labels = {
            "bl_part_num": "BL Part Number",
            "bl_color_id": "BL Color ID",
            "part_num": "RB Part Number",
            "part_name": "Name",
            "color_id": "RB Color ID",
            "color_name": "Color",
            "required": "Required",
            "have": "Have",
            "variation": "Variation choice",
            "counterpart": "Counterpart",
            "mold_variations": "Mold variations",
            "alternatives": "Alternatives",
        }
        # Sized for the content rather than the window. Every column is
        # stretch=False below so these widths survive, which is what keeps the
        # horizontal scrollbar live instead of letting Tk squeeze the text.
        widths = {
            "bl_part_num": 120,
            "bl_color_id": 100,
            "part_num": 120,
            "part_name": 300,
            "color_id": 100,
            "color_name": 130,
            "required": 75,
            "have": 75,
            "counterpart": 170,
            "mold_variations": 220,
            "alternatives": 220,
            "variation": 340,
        }

        toolbar = ttk.Frame(parent)
        toolbar.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 6),
        )
        ttk.Label(toolbar, text="Search required parts:").pack(
            side="left",
        )
        search_entry = ttk.Entry(
            toolbar,
            textvariable=self.parts_search_var,
            width=34,
        )
        search_entry.pack(side="left", padx=(6, 6))
        search_entry.bind(
            "<KeyRelease>",
            self._schedule_parts_search,
        )
        ttk.Button(
            toolbar,
            text="Clear",
            command=self._clear_parts_search,
        ).pack(side="left", padx=(0, 12))
        ttk.Label(
            toolbar,
            text=(
                "After loading, optionally prefill a reviewed Brickognize "
                "CSV, or double-click a Have value to edit the total, then "
                "click Compare Parts."
            ),
        ).pack(side="left")

        column_button = ttk.Menubutton(
            toolbar,
            text="Columns",
        )
        column_button.pack(side="right")
        column_menu = tk.Menu(
            column_button,
            tearoff=False,
        )
        column_button.configure(menu=column_menu)

        column_options = [
            ("image", "Image"),
            *self.part_heading_labels.items(),
        ]
        for column, label in column_options:
            variable = tk.BooleanVar(
                value=self._column_visible_by_default(column)
            )
            self.parts_column_vars[column] = variable
            column_menu.add_checkbutton(
                label=label,
                variable=variable,
                command=self._update_visible_part_columns,
            )

        column_menu.add_separator()
        column_menu.add_command(
            label="Show all columns",
            command=self._show_all_part_columns,
        )

        ttk.Label(
            toolbar,
            text="Shift + scroll moves the table sideways.",
        ).pack(side="right", padx=(0, 12))

        for column in columns:
            self.parts_tree.heading(
                column,
                text=self.part_heading_labels[column],
                command=lambda selected_column=column: (
                    self._sort_parts_table(selected_column)
                ),
            )
            self.parts_tree.column(
                column,
                width=widths[column],
                minwidth=60,
                stretch=False,
                anchor="center" if column in {
                    "bl_part_num",
                    "bl_color_id",
                    "part_num",
                    "color_id",
                    "required",
                    "have",
                } else "w",
            )

        vertical = ttk.Scrollbar(
            parent,
            orient="vertical",
            command=self.parts_tree.yview,
        )
        horizontal = ttk.Scrollbar(
            parent,
            orient="horizontal",
            command=self.parts_tree.xview,
        )
        self.parts_tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        self._bind_fast_horizontal_scroll(self.parts_tree)

        self.parts_tree.grid(row=1, column=0, sticky="nsew")
        vertical.grid(row=1, column=1, sticky="ns")
        horizontal.grid(row=2, column=0, sticky="ew")
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        # Highlight parts whose quantity came from an accepted variation, and
        # parts with a variation the user chose not to use.
        substituted_color = "#d8f0d8"
        declined_color = "#fdf3cd"

        self.parts_tree.tag_configure(
            "substituted",
            background=substituted_color,
        )
        self.parts_tree.tag_configure(
            "variation_declined",
            background=declined_color,
        )

        # A colour on its own tells the user nothing, so name both of them.
        legend = ttk.Frame(parent)
        legend.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        for color, text in (
            (substituted_color, "Variation kept, counted toward the set part"),
            (declined_color, "Variation skipped, left as a separate row"),
        ):
            swatch = ttk.Frame(legend)
            swatch.pack(side="left", padx=(0, 4))
            tk.Label(
                swatch,
                background=color,
                relief="solid",
                borderwidth=1,
                width=3,
            ).pack()
            ttk.Label(legend, text=text).pack(side="left", padx=(0, 16))

        self.review_variations_button = ttk.Button(
            legend,
            text="Review variation allocations",
            command=self.review_variation_choices,
            state="disabled",
        )
        self.review_variations_button.pack(side="right")

        self.parts_tree.bind(
            "<Double-1>",
            self._begin_quantity_edit,
        )
        self.parts_tree.bind(
            "<<TreeviewSelect>>",
            self._show_selected_part_summary,
        )

    def _build_relationship_details(self, parent):
        relationship_toolbar = ttk.Frame(parent)
        relationship_toolbar.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 6),
        )
        ttk.Label(
            relationship_toolbar,
            text=(
                "Select a part above, then fetch names for its "
                "mold variations and alternatives."
            ),
        ).pack(side="left")

        self.open_bricklink_catalog_button = ttk.Button(
            relationship_toolbar,
            text="Open Selected Part in BrickLink",
            command=self.open_selected_relationship_in_bricklink,
            state="disabled",
        )
        self.open_bricklink_catalog_button.pack(side="right")

        ttk.Button(
            relationship_toolbar,
            text="Fetch selected part details",
            command=self.fetch_selected_relationship_details,
        ).pack(side="right", padx=(0, 8))

        relationship_columns = (
            "relationship",
            "part_num",
            "bricklink_part_num",
            "part_name",
            "same_color",
            "available_colors",
            "available_color_ids",
        )

        self.relationship_tree = ttk.Treeview(
            parent,
            columns=relationship_columns,
            show="tree headings",
            style="Parts.Treeview",
        )
        self.relationship_tree.heading("#0", text="Image", anchor="center",)
        self.relationship_tree.column(
            "#0",
            width=82,
            minwidth=82,
            stretch=False,
            anchor="center",
        )

        relationship_headings = {
            "relationship": "Relationship",
            "part_num": "RB Part Number",
            "bricklink_part_num": "BrickLink Part Number",
            "part_name": "Part Name",
            "same_color": "Same Color?",
            "available_colors": "Available Colors",
            "available_color_ids": "Color IDs",
        }

        relationship_widths = {
            "relationship": 130,
            "part_num": 140,
            "bricklink_part_num": 165,
            "part_name": 300,
            "same_color": 100,
            "available_colors": 400,
            "available_color_ids": 220,
        }

        centered_columns = {
            "part_num",
            "bricklink_part_num",
            "same_color",
            "available_color_ids",
        }

        for column in relationship_columns:
            anchor = (
                "center"
                if column in centered_columns
                else "w"
            )

            self.relationship_tree.heading(
                column,
                text=relationship_headings[column],
                anchor=anchor,
            )

            self.relationship_tree.column(
                column,
                width=relationship_widths[column],
                minwidth=80,
                anchor=anchor,
            )

        relationship_vertical = ttk.Scrollbar(
            parent,
            orient="vertical",
            command=self.relationship_tree.yview,
        )

        relationship_horizontal = ttk.Scrollbar(
            parent,
            orient="horizontal",
            command=self.relationship_tree.xview,
        )

        self.relationship_tree.configure(
            yscrollcommand=relationship_vertical.set,
            xscrollcommand=relationship_horizontal.set,
        )
        self._bind_fast_horizontal_scroll(self.relationship_tree)
        self.relationship_tree.bind(
            "<<TreeviewSelect>>",
            self._update_bricklink_catalog_button,
        )

        self.relationship_tree.grid(
            row=1,
            column=0,
            sticky="nsew",
        )
        relationship_vertical.grid(
            row=1,
            column=1,
            sticky="ns",
        )
        relationship_horizontal.grid(
            row=2,
            column=0,
            sticky="ew",
        )

        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

    def _build_report_table(self, parent):
        report_columns = (
            "status",
            "bl_part_num",
            "bl_color_id",
            "part_num",
            "part_name",
            "color_id",
            "color",
            "required",
            "have",
            "missing",
            "extra",
            "removal",
            "xml_remark",
            "note",
        )
        self.report_tree = ttk.Treeview(
            parent,
            columns=report_columns,
            displaycolumns=tuple(
                column
                for column in report_columns
                if self._column_visible_by_default(column)
            ),
            show="tree headings",
            selectmode="extended",
            style="Parts.Treeview",
        )

        self.report_tree.heading(
            "#0",
            text="Image",
            anchor="center",
        )

        self.report_tree.column(
            "#0",
            width=82,
            minwidth=82,
            stretch=False,
            anchor="center",
        )

        self.report_heading_labels = {
            "status": "Status",
            "bl_part_num": "BL Part Number",
            "bl_color_id": "BL Color ID",
            "part_num": "RB Part Number",
            "part_name": "Name",
            "color_id": "RB Color ID",
            "color": "Color",
            "required": "Required",
            "have": "Have",
            "missing": "Missing",
            "extra": "Extra",
            "removal": "Removal",
            "xml_remark": "Bag / XML Remark",
            "note": "Note",
        }
        # Sized for the content rather than the window; see the note in
        # _build_parts_table about stretch=False and horizontal scrolling.
        report_widths = {
            "status": 120,
            "bl_part_num": 125,
            "bl_color_id": 100,
            "part_num": 125,
            "part_name": 300,
            "color_id": 100,
            "color": 120,
            "required": 80,
            "have": 80,
            "missing": 80,
            "extra": 80,
            "removal": 115,
            "xml_remark": 220,
            "note": 520,
        }
        centered_columns = {
            "bl_part_num",
            "bl_color_id",
            "part_num",
            "color_id",
            "required",
            "have",
            "missing",
            "extra",
            "removal",
        }

        toolbar = ttk.Frame(parent)
        toolbar.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 6),
        )
        ttk.Label(toolbar, text="Search report:").pack(side="left")
        search_entry = ttk.Entry(
            toolbar,
            textvariable=self.report_search_var,
            width=34,
        )
        search_entry.pack(side="left", padx=(6, 6))
        search_entry.bind(
            "<KeyRelease>",
            self._schedule_report_search,
        )
        ttk.Button(
            toolbar,
            text="Clear",
            command=self._clear_report_search,
        ).pack(side="left", padx=(0, 12))
        ttk.Label(
            toolbar,
            text="Edit missing-part remarks or review surplus removals.",
        ).pack(side="left")

        self.finish_verification_button = ttk.Button(
            toolbar,
            text="Finish Verification",
            style="Primary.TButton",
            command=self._handle_report_stage_action,
            state="disabled",
        )
        self.finish_verification_button.pack(side="right", padx=(8, 0))

        self.bulk_remark_button = ttk.Button(
            toolbar,
            text="Assign Bag / Remark...",
            command=self.assign_bulk_report_remark,
            state="disabled",
        )
        self.bulk_remark_button.pack(side="right", padx=(8, 0))

        self.removal_checklist_button = ttk.Button(
            toolbar,
            text="Removal Checklist",
            command=self.open_removal_checklist,
            state="disabled",
        )
        self.removal_checklist_button.pack(side="right", padx=(8, 0))

        column_button = ttk.Menubutton(
            toolbar,
            text="Columns",
        )
        column_button.pack(side="right")
        column_menu = tk.Menu(
            column_button,
            tearoff=False,
        )
        column_button.configure(menu=column_menu)

        column_options = [
            ("image", "Image"),
            *self.report_heading_labels.items(),
        ]
        for column, label in column_options:
            variable = tk.BooleanVar(
                value=self._column_visible_by_default(column)
            )
            self.report_column_vars[column] = variable
            column_menu.add_checkbutton(
                label=label,
                variable=variable,
                command=self._update_visible_report_columns,
            )

        column_menu.add_separator()
        column_menu.add_command(
            label="Show all columns",
            command=self._show_all_report_columns,
        )

        ttk.Label(
            toolbar,
            text="Shift + scroll moves the table sideways.",
        ).pack(side="right", padx=(0, 12))

        for column in report_columns:
            anchor = (
                "center"
                if column in centered_columns
                else "w"
                )

            self.report_tree.heading(
                column,
                text=self.report_heading_labels[column],
                anchor=anchor,
                command=lambda selected_column=column: (
                    self._sort_report_table(selected_column)
                ),
            )

            self.report_tree.column(
                column,
                width=report_widths[column],
                minwidth=60,
                stretch=False,
                anchor=anchor,
            )

        report_vertical = ttk.Scrollbar(
            parent,
            orient="vertical",
            command=self.report_tree.yview,
        )
        report_horizontal = ttk.Scrollbar(
            parent,
            orient="horizontal",
            command=self.report_tree.xview,
        )
        self.report_tree.configure(
            yscrollcommand=report_vertical.set,
            xscrollcommand=report_horizontal.set,
        )
        self._bind_fast_horizontal_scroll(self.report_tree)
        self.report_tree.bind(
            "<Double-1>",
            self._begin_report_remark_edit,
        )
        self.report_tree.bind(
            "<<TreeviewSelect>>",
            lambda _event: self._update_bulk_remark_button(),
        )
        self.report_tree.tag_configure("removed", foreground="#666666")
        self.report_tree.grid(row=1, column=0, sticky="nsew")
        report_vertical.grid(row=1, column=1, sticky="ns")
        report_horizontal.grid(row=2, column=0, sticky="ew")
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

    # =========================================================================
    # Set loading and saved jobs
    # =========================================================================
    def start_new_job(self, set_num=None):
        """Load a set number supplied by the Start New Set dialog."""
        if set_num is None:
            self.open_start_new_set_dialog()
            return
        set_num = str(set_num).strip()
        if not set_num:
            messagebox.showwarning(
                "Set number required",
                "Enter a LEGO set number first.",
                parent=self.root,
            )
            return

        self._run_background(
            status=f"Loading set {set_num}...",
            work=lambda: self._load_set_data(set_num),
            on_success=lambda result: self._finish_new_job(
                set_num,
                result,
            ),
        )

    def _load_set_data(self, set_num):
        set_details = get_set_details(set_num)
        parts_df = get_set_parts(
            set_num,
            include_minifig_parts=True,
        )
        required_parts_df = parts_df[
            parts_df["is_spare"] == False
        ].copy()

        display_df = attach_variation_relationships(
            required_parts_df,
            self.relationships_df,
        )
        display_df = attach_counterpart_groups(
            display_df,
            self.relationships_df,
        )

        display_df = attach_print_family_metadata(
            display_df,
            self.relationships_df,
        )

        # BrickLink values are what the client uploads, so resolve them here on
        # the background thread rather than during the compare step.
        display_df = attach_bricklink_ids(display_df)

        display_df = display_df.sort_values(
            by=[
                "has_counterpart",
                "counterpart_group_id",
                "part_num",
                "color_name",
            ],
            ascending=[False, True, True, True],
            kind="stable",
        ).reset_index(drop=True)

        return set_details, display_df
    
    def _finish_new_job(self, requested_set_num, result):
        set_details, display_df = result
        display_df, _selected_count = self._review_ambiguous_bricklink_mappings(
            display_df
        )
        self.set_num = self._base_set_num(requested_set_num)
        self.set_details = set_details
        self.parts_df = display_df

        self.user_input_df = display_df[
            ["part_num", "color_id"]
        ].drop_duplicates().copy()
        self.user_input_df["quantity_have"] = 0

        self.substitutions = []
        self.declined_substitutions = []
        self.variation_proposals = None
        self.import_have_df = None
        self.import_history = []
        self.extra_part_metadata_df = pd.DataFrame(
            columns=self.extra_part_metadata_columns
        )
        self.wanted_list_remarks_df = pd.DataFrame(
            columns=self.wanted_list_remark_columns
        )
        self.removal_states_df = pd.DataFrame(
            columns=self.removal_state_columns
        )
        self.workflow_stage = self.parts_entry_stage
        self.job_dirty = False
        self._update_review_variations_button()
        self.job_id = make_job_id(self.set_num)
        self.parts_sort_column = None
        self.parts_sort_ascending = True
        self.parts_search_var.set("")
        self.set_num_var.set(self.set_num)

        self._update_set_title()
        self._render_parts_table()
        self.relationship_image_generation += 1
        self._clear_relationship_details()
        self._invalidate_report()
        self.status_var.set(
            f"Loaded {len(display_df)} required part/color rows."
        )

        unresolved_count = len(self._ambiguous_bricklink_mappings(display_df))
        if unresolved_count:
            self.status_var.set(
                f"Loaded {len(display_df)} required part/color rows; "
                f"{unresolved_count} BrickLink mapping(s) still require review."
            )

    @staticmethod
    def _ambiguous_bricklink_mappings(parts_df):
        """Return one review record per part with multiple BrickLink IDs."""
        if parts_df is None or parts_df.empty:
            return []
        if "bricklink_part_candidates" not in parts_df.columns:
            return []

        records = []
        seen = set()
        for _, row in parts_df.iterrows():
            part_num = str(row.get("part_num", "")).strip()
            if not part_num or part_num in seen:
                continue
            selected = row.get("bricklink_part_num", "")
            selected = (
                ""
                if selected is None or pd.isna(selected)
                else str(selected).strip()
            )
            candidates = parse_bricklink_candidates(
                row.get("bricklink_part_candidates", "")
            )
            if selected or len(candidates) < 2:
                continue

            part_name = row.get("part_name", "")
            records.append({
                "rebrickable_part_num": part_num,
                "part_name": (
                    ""
                    if part_name is None or pd.isna(part_name)
                    else str(part_name)
                ),
                "candidates": candidates,
            })
            seen.add(part_num)
        return records

    def _review_ambiguous_bricklink_mappings(self, parts_df):
        """Apply and cache optional operator choices for ambiguous mappings."""
        result_df = parts_df.copy()
        ambiguity_records = self._ambiguous_bricklink_mappings(result_df)
        if not ambiguity_records:
            return result_df, 0

        choices = self._choose_bricklink_part_mappings(ambiguity_records)
        if choices is None:
            return result_df, 0

        record_lookup = {
            record["rebrickable_part_num"]: record
            for record in ambiguity_records
        }
        applied_count = 0
        for rebrickable_part_num, bricklink_part_num in choices.items():
            record = record_lookup.get(rebrickable_part_num)
            if record is None:
                continue
            try:
                save_manual_part_mapping(
                    rebrickable_part_num,
                    bricklink_part_num,
                    part_name=record["part_name"],
                    candidates=record["candidates"],
                )
            except (OSError, TypeError, ValueError) as exc:
                messagebox.showerror(
                    "Could not save BrickLink mapping",
                    str(exc),
                    parent=self.root,
                )
                continue

            matching_rows = (
                result_df["part_num"].astype(str) == rebrickable_part_num
            )
            result_df.loc[matching_rows, "bricklink_part_num"] = (
                bricklink_part_num
            )
            applied_count += 1

        return result_df, applied_count

    def review_bricklink_mappings(self):
        """Reopen candidate review for unresolved required-part mappings."""
        if self.busy:
            return self._show_busy_action_message("BrickLink mapping review")
        if self.parts_df is None:
            messagebox.showinfo(
                "No set loaded",
                "Load or resume a set before reviewing BrickLink mappings.",
                parent=self.root,
            )
            return
        if self.workflow_stage == self.complete_stage:
            messagebox.showinfo(
                "Verification is complete",
                "Reopen the verification before changing BrickLink mappings.",
                parent=self.root,
            )
            return

        if not self._ambiguous_bricklink_mappings(self.parts_df):
            messagebox.showinfo(
                "No mappings to review",
                "No required parts have multiple unresolved BrickLink IDs.",
                parent=self.root,
            )
            return

        updated_df, applied_count = self._review_ambiguous_bricklink_mappings(
            self.parts_df
        )
        if not applied_count:
            self._update_command_states()
            return

        self.parts_df = updated_df
        self._render_parts_table()
        if self.report_df is not None:
            self._invalidate_report()
        else:
            self._update_command_states()
        self._mark_job_dirty()

        unresolved_count = len(
            self._ambiguous_bricklink_mappings(self.parts_df)
        )
        self.status_var.set(
            f"Saved {applied_count} manual BrickLink mapping(s); "
            f"{unresolved_count} still require review."
        )

    def _choose_bricklink_part_mappings(self, ambiguity_records):
        """Let the operator inspect candidates and choose trusted IDs."""
        result = {"choices": None}
        choices = {}
        explicitly_unresolved = set()
        candidate_rows = {}
        part_items = {}

        dialog = tk.Toplevel(self.root)
        dialog.title("Review BrickLink Part Mappings")
        dialog.transient(self.root)
        dialog.resizable(True, True)

        content = ttk.Frame(dialog, padding=14)
        content.grid(row=0, column=0, sticky="nsew")
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)
        content.columnconfigure(0, weight=1)

        ttk.Label(
            content,
            text=(
                "Rebrickable returned multiple BrickLink IDs for these parts. "
                "Open candidates in the BrickLink catalog, select the correct "
                "one, then apply your selections. Parts left unresolved will "
                "be excluded from BrickLink XML."
            ),
            justify="left",
            wraplength=820,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 10))

        columns = ("status", "rb_part", "part_name", "candidate")
        row_count = sum(
            len(record["candidates"])
            for record in ambiguity_records
        )
        tree = ttk.Treeview(
            content,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=min(14, max(5, row_count)),
        )
        headings = {
            "status": "Choice",
            "rb_part": "RB Part Number",
            "part_name": "Part Name",
            "candidate": "Candidate BL Part Number",
        }
        widths = {
            "status": 110,
            "rb_part": 135,
            "part_name": 330,
            "candidate": 190,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(
                column,
                width=widths[column],
                minwidth=80,
                stretch=(column == "part_name"),
            )
        tree.grid(row=1, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            content,
            orient="vertical",
            command=tree.yview,
        )
        scrollbar.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)

        for record_index, record in enumerate(ambiguity_records):
            part_num = record["rebrickable_part_num"]
            part_items[part_num] = []
            for candidate_index, candidate in enumerate(record["candidates"]):
                item_id = f"mapping_{record_index}_{candidate_index}"
                candidate_rows[item_id] = {
                    "part_num": part_num,
                    "candidate": candidate,
                }
                part_items[part_num].append(item_id)
                tree.insert(
                    "",
                    "end",
                    iid=item_id,
                    values=(
                        "Review required",
                        part_num,
                        record["part_name"],
                        candidate,
                    ),
                )

        button_row = ttk.Frame(content)
        button_row.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(10, 0),
        )

        def selected_record():
            selection = tree.selection()
            return candidate_rows.get(selection[0]) if selection else None

        def refresh_part(part_num):
            chosen_candidate = choices.get(part_num)
            for item_id in part_items[part_num]:
                row = candidate_rows[item_id]
                if row["candidate"] == chosen_candidate:
                    status = "Selected"
                elif part_num in explicitly_unresolved:
                    status = "Unresolved"
                else:
                    status = ""
                tree.set(item_id, "status", status)

        def open_selected():
            row = selected_record()
            if row is not None:
                self._open_bricklink_catalog_part(
                    row["candidate"],
                    parent=dialog,
                )

        def use_selected():
            row = selected_record()
            if row is None:
                return
            choices[row["part_num"]] = row["candidate"]
            explicitly_unresolved.discard(row["part_num"])
            refresh_part(row["part_num"])

        def leave_unresolved():
            row = selected_record()
            if row is None:
                return
            choices.pop(row["part_num"], None)
            explicitly_unresolved.add(row["part_num"])
            refresh_part(row["part_num"])

        def apply_selections():
            result["choices"] = dict(choices)
            dialog.destroy()

        def cancel():
            result["choices"] = None
            dialog.destroy()

        open_button = ttk.Button(
            button_row,
            text="Open Selected Candidate in BrickLink",
            command=open_selected,
            state="disabled",
        )
        open_button.pack(side="left")
        use_button = ttk.Button(
            button_row,
            text="Use Selected ID",
            command=use_selected,
            state="disabled",
        )
        use_button.pack(side="left", padx=(8, 0))
        unresolved_button = ttk.Button(
            button_row,
            text="Leave Part Unresolved",
            command=leave_unresolved,
            state="disabled",
        )
        unresolved_button.pack(side="left", padx=(8, 0))
        ttk.Button(
            button_row,
            text="Apply Selections",
            command=apply_selections,
        ).pack(side="right")
        ttk.Button(
            button_row,
            text="Cancel",
            command=cancel,
        ).pack(side="right", padx=(0, 8))

        def update_candidate_buttons(_event=None):
            state = "normal" if selected_record() is not None else "disabled"
            open_button.configure(state=state)
            use_button.configure(state=state)
            unresolved_button.configure(state=state)

        tree.bind("<<TreeviewSelect>>", update_candidate_buttons)
        tree.bind("<Double-1>", lambda _event: open_selected())
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Escape>", lambda _event: cancel())
        dialog.grab_set()
        first_item = next(iter(candidate_rows), None)
        if first_item:
            tree.selection_set(first_item)
            tree.focus(first_item)
            update_candidate_buttons()
        dialog.minsize(800, 340)
        self.root.wait_window(dialog)
        return result["choices"]

    @staticmethod
    def _base_set_num(set_num):
        """Return the user-facing set number without its version suffix."""
        return str(set_num).strip().split("-", 1)[0]
    
    def _saved_job_sort_key(self, job):
        """Sort by status, newest update time, then set number."""
        status_priority = {
            "in_progress": 0,
            "complete": 1,
        }

        status = str(job.get("status", "")).strip().casefold()
        updated_at = pd.to_datetime(
            job.get("updated_at"),
            errors="coerce",
            utc=True,
        )
        updated_timestamp = (
            updated_at.timestamp()
            if pd.notna(updated_at)
            else 0
        )
        set_text = self._base_set_num(job.get("set_num", ""))
        try:
            set_number = int(set_text)
        except (TypeError, ValueError):
            set_number = float("inf")

        return (
            status_priority.get(status, 2),
            -updated_timestamp,
            set_number,
            set_text.casefold(),
        )

    @staticmethod
    def _display_job_status(status):
        return str(status or "unknown").replace("_", " ").title()

    @staticmethod
    def _display_job_timestamp(updated_at):
        parsed = pd.to_datetime(updated_at, errors="coerce")
        if pd.isna(parsed):
            return str(updated_at or "Unknown")
        return parsed.strftime("%Y-%m-%d %H:%M")

    def _saved_job_label(self, job):
        return (
            f"Set {job.get('set_num', '?')} - "
            f"{self._display_job_status(job.get('status'))} - "
            f"{self._display_job_timestamp(job.get('updated_at'))}"
        )

    def _saved_job_search_text(self, job):
        return " ".join((
            self._saved_job_label(job),
            str(job.get("job_id", "")),
        ))

    def _refresh_saved_jobs(self):
        self.saved_job_lookup = {}

        try:
            self.saved_jobs = sorted(
                list_jobs(JOBS_DIR),
                key=self._saved_job_sort_key)
        except (OSError, ValueError) as exc:
            self.status_var.set(f"Could not list saved jobs: {exc}")
            self.saved_jobs = []

        self.resume_jobs_menu.delete(0, "end")
        for job in self.saved_jobs[:12]:
            label = self._saved_job_label(job)
            self.saved_job_lookup[label] = job.get("job_id")
            self.resume_jobs_menu.add_command(
                label=label,
                command=lambda job_id=job.get("job_id"): (
                    self.resume_selected_job(job_id)
                ),
            )

        if self.saved_jobs:
            self.resume_jobs_menu.add_separator()
            self.resume_jobs_menu.add_command(
                label="View All Saved Jobs...",
                command=self.open_saved_jobs_dialog,
            )
            self.saved_job_var.set(
                self._saved_job_label(self.saved_jobs[0])
            )
        else:
            self.resume_jobs_menu.add_command(
                label="(No saved jobs)",
                state="disabled",
            )
            self.saved_job_var.set("")
        self._render_saved_jobs_dialog()
        self._update_command_states()

    def resume_selected_job(self, job_id=None, confirm_leave=True):
        if job_id is None:
            label = self.saved_job_var.get()
            job_id = self.saved_job_lookup.get(label)
        if not job_id:
            messagebox.showwarning(
                "Saved job required",
                "Choose a saved job first.",
                parent=self.root,
            )
            return

        if confirm_leave and not self._confirm_leave_current_job():
            return

        try:
            job_data = load_job(JOBS_DIR, job_id)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(
                "Could not resume job",
                str(exc),
                parent=self.root,
            )
            return

        set_num = self._base_set_num(job_data["set_num"])
        self._run_background(
            status=f"Resuming set {set_num}...",
            work=lambda: self._load_resume_data(set_num, job_data),
            on_success=lambda result: self._finish_resume_job(
                job_id,
                job_data,
                result,
            ),
        )

    def _load_resume_data(self, set_num, job_data):
        """Load a set and restore everything needed to re-review variations."""
        set_details, display_df = self._load_set_data(set_num)

        proposals_df = job_data.get("variation_proposals_df")
        if proposals_df is None or proposals_df.empty:
            proposals_df = self._reconstruct_variation_proposals(
                job_data,
                display_df,
            )
        else:
            proposals_df = proposals_df.copy()

        import_have_df = job_data.get("import_have_df")
        if (
            (import_have_df is None or import_have_df.empty)
            and not proposals_df.empty
        ):
            import_have_df = self._reconstruct_import_have(job_data)
        elif import_have_df is None:
            import_have_df = pd.DataFrame()
        else:
            import_have_df = import_have_df.copy()

        resolved_image_columns = {
            "csv_image_url",
            "csv_image_color_matched",
            "set_image_url",
            "set_image_color_matched",
        }
        if not resolved_image_columns.issubset(proposals_df.columns):
            proposals_df = self._attach_proposal_images(
                proposals_df,
                parts_df=display_df,
            )
        return set_details, display_df, proposals_df, import_have_df

    def _reconstruct_variation_proposals(self, job_data, display_df):
        """Rebuild offers for jobs saved before proposal persistence existed."""
        columns = [
            "csv_part_num",
            "csv_color_id",
            "quantity_have",
            "set_part_num",
            "set_part_name",
            "set_color_id",
            "set_color_name",
            "quantity_required",
            "rel_type",
        ]
        records = [
            *(job_data.get("substitutions") or []),
            *(job_data.get("declined_substitutions") or []),
        ]
        if not records:
            return pd.DataFrame(columns=columns)

        detail_lookup = {}
        for _, row in display_df.iterrows():
            part_name = row.get("part_name", "")
            color_name = row.get("color_name", "")
            quantity_required = int(row.get("quantity_required", 0) or 0)
            key = (
                str(row["part_num"]),
                self._color_key(row.get("color_id")),
            )
            detail_lookup.setdefault(
                key,
                (
                    "" if pd.isna(part_name) else str(part_name),
                    "" if pd.isna(color_name) else str(color_name),
                    quantity_required,
                ),
            )

        proposals = []
        seen = set()
        for record in records:
            csv_part_num = str(record.get("csv_part_num", "")).strip()
            set_part_num = str(record.get("set_part_num", "")).strip()
            set_color_id = self._color_key(record.get("set_color_id"))
            csv_color_id = self._color_key(
                record.get("csv_color_id", set_color_id)
            )
            key = (csv_part_num, csv_color_id, set_part_num, set_color_id)
            if (
                not csv_part_num
                or not set_part_num
                or csv_color_id is None
                or set_color_id is None
            ):
                continue
            if key in seen:
                continue
            seen.add(key)

            part_name, color_name, quantity_required = detail_lookup.get(
                (set_part_num, set_color_id),
                ("", "", 0),
            )
            proposals.append({
                "csv_part_num": csv_part_num,
                "csv_color_id": csv_color_id,
                "quantity_have": int(record.get("quantity_have", 0) or 0),
                "set_part_num": set_part_num,
                "set_part_name": part_name,
                "set_color_id": set_color_id,
                "set_color_name": color_name,
                "quantity_required": quantity_required,
                "rel_type": str(record.get("rel_type", "")),
            })

        return pd.DataFrame(proposals, columns=columns)

    def _reconstruct_import_have(self, job_data):
        """Best-effort reconstruction of the untouched import for legacy jobs."""
        accepted = job_data.get("substitutions") or []
        all_records = [
            *accepted,
            *(job_data.get("declined_substitutions") or []),
        ]
        current_df = combine_entry_csv(job_data.get("user_input_df"))
        current_lookup = {
            (str(row["part_num"]), self._color_key(row["color_id"])):
                int(row["quantity_have"])
            for _, row in current_df.iterrows()
        }

        moved_to_targets = {}
        for record in accepted:
            target_key = (
                str(record.get("set_part_num", "")),
                self._color_key(record.get("set_color_id")),
            )
            moved_to_targets[target_key] = (
                moved_to_targets.get(target_key, 0)
                + self._record_allocated_quantity(record)
            )

        reconstructed = {}
        for record in all_records:
            set_color_id = self._color_key(record.get("set_color_id"))
            csv_color_id = self._color_key(
                record.get("csv_color_id", set_color_id)
            )
            source_key = (
                str(record.get("csv_part_num", "")),
                csv_color_id,
            )
            target_key = (
                str(record.get("set_part_num", "")),
                set_color_id,
            )
            reconstructed[source_key] = max(
                reconstructed.get(source_key, 0),
                int(record.get("quantity_have", 0) or 0),
            )
            reconstructed[target_key] = max(
                0,
                current_lookup.get(target_key, 0)
                - moved_to_targets.get(target_key, 0),
            )

        return pd.DataFrame([
            {
                "part_num": part_num,
                "color_id": color_id,
                "quantity_have": quantity,
            }
            for (part_num, color_id), quantity in reconstructed.items()
        ])

    def _finish_resume_job(self, job_id, job_data, result):
        set_details, display_df, proposals_df, import_have_df = result
        display_df, selected_mapping_count = (
            self._review_ambiguous_bricklink_mappings(
                display_df
            )
        )
        self._close_removal_checklist()
        self.job_id = job_id
        self.set_num =  self._base_set_num(job_data["set_num"])
        self.set_details = set_details
        self.parts_df = display_df

        required_keys_df = display_df[
            ["part_num", "color_id"]
        ].drop_duplicates().copy()
        required_keys_df["quantity_have"] = 0
        self.user_input_df = fill_blank_quantities(
            required_keys_df,
            job_data.get("user_input_df"),
        )
        self.substitutions = list(job_data.get("substitutions") or [])
        self.declined_substitutions = list(
            job_data.get("declined_substitutions") or []
        )
        self.variation_proposals = proposals_df
        self.import_have_df = import_have_df
        self.import_history = list(job_data.get("import_history") or [])
        self.extra_part_metadata_df = job_data.get(
            "extra_part_metadata_df",
            pd.DataFrame(columns=self.extra_part_metadata_columns),
        ).copy()
        self.wanted_list_remarks_df = job_data.get(
            "wanted_list_remarks_df",
            pd.DataFrame(columns=self.wanted_list_remark_columns),
        ).copy()
        self.removal_states_df = job_data.get(
            "removal_states_df",
            pd.DataFrame(columns=self.removal_state_columns),
        ).copy()
        self.job_dirty = False
        self._update_review_variations_button()
        self.parts_sort_column = None
        self.parts_sort_ascending = True
        self.parts_search_var.set("")
        self.set_num_var.set(self.set_num)

        self._update_set_title()
        self._render_parts_table()
        self.relationship_image_generation += 1
        self._clear_relationship_details()
        if selected_mapping_count:
            # The saved report was built with an unresolved part number. A
            # newly confirmed mapping changes its XML identity, so never revive
            # that stale snapshot (including from a previously complete job).
            self._invalidate_report()
            self.job_dirty = True
            restored_report = False
        else:
            restored_report = self._restore_saved_workflow(job_data)
        self._update_review_variations_button()
        if restored_report:
            stage_label = (
                "completed verification"
                if self.workflow_stage == self.complete_stage
                else "verification report review"
            )
            self.status_var.set(
                f"Resumed saved job {job_id} at {stage_label}."
            )
        else:
            if selected_mapping_count:
                self.status_var.set(
                    f"Resumed saved job {job_id}. Saved "
                    f"{selected_mapping_count} manual BrickLink mapping(s). "
                    "Compare Parts to generate the verification report."
                )
            else:
                self.status_var.set(
                    f"Resumed saved job {job_id}. Compare Parts to generate "
                    "the verification report."
                )

    def _restore_saved_workflow(self, job_data):
        """Restore a saved report stage, or safely fall back to parts entry."""
        saved_stage = job_data.get("workflow_stage")
        has_snapshots = (
            bool(job_data.get("has_report_snapshot"))
            and bool(job_data.get("has_wanted_list_snapshot"))
        )
        can_restore_report = (
            saved_stage in {
                self.report_review_stage,
                self.complete_stage,
            }
            and has_snapshots
        )

        self.report_search_var.set("")
        self.report_sort_column = None
        self.report_sort_ascending = True
        if not can_restore_report:
            self._invalidate_report()
            self.main_table_notebook.select(self.parts_tab)
            return False

        self.workflow_stage = saved_stage
        self.report_df = job_data.get(
            "report_snapshot_df",
            pd.DataFrame(),
        ).copy()
        self.report_df = self._attach_removal_states(
            self.report_df,
            self.removal_states_df,
        )
        self._sync_removal_states_from_report()
        self.wanted_list_df = job_data.get(
            "wanted_list_snapshot_df",
            pd.DataFrame(),
        ).copy()
        self._render_report()
        self.main_table_notebook.select(self.report_tab)
        self._update_command_states()
        return True

    def _update_set_title(self):
        set_name = (self.set_details or {}).get("name", "")
        self.set_title_var.set(f"Set {self.set_num}: {set_name}")

    # =========================================================================
    # Quantity entry and CSV prefill
    # =========================================================================
    def _schedule_parts_search(self, _event=None):
        """Debounce live searching so large tables remain responsive."""
        if self.parts_search_after_id is not None:
            self.root.after_cancel(self.parts_search_after_id)

        self.parts_search_after_id = self.root.after(
            250,
            self._apply_parts_search,
        )

    def _apply_parts_search(self):
        self.parts_search_after_id = None
        if self.parts_df is None:
            return

        self._render_parts_table()
        query = self.parts_search_var.get().strip()
        if query:
            self.status_var.set(
                f'Search "{query}": showing {self.visible_parts_count} '
                f"of {self.total_parts_count} required rows."
            )
        else:
            self.status_var.set(
                f"Showing all {self.total_parts_count} required rows."
            )

    def _clear_parts_search(self):
        self.parts_search_var.set("")
        self._apply_parts_search()

    def _filter_editable_parts(self, editable_df):
        """Search across all useful required-parts fields."""
        query = self.parts_search_var.get().strip().casefold()
        if not query:
            return editable_df

        search_columns = [
            "bricklink_part_num",
            "bricklink_color_id",
            "part_num",
            "part_name",
            "color_id",
            "color_name",
            "quantity_required",
            "quantity_have",
            "counterpart",
            "mold_variations",
            "alternatives",
            "variation_note",
        ]
        matches = pd.Series(
            False,
            index=editable_df.index,
            dtype=bool,
        )

        for column in search_columns:
            if column not in editable_df.columns:
                continue

            values = (
                editable_df[column]
                .astype("string")
                .fillna("")
                .str.casefold()
            )
            matches = matches | values.str.contains(
                query,
                regex=False,
                na=False,
            )
        # When one member of a print family matches, show the complete family so the
# summary total and its child rows remain understandable.
        if {
            "has_print_family",
            "print_family_id",
        }.issubset(editable_df.columns):

            family_mask = (
                editable_df["has_print_family"]
                .fillna(False)
                .astype(bool)
            )

            matched_family_ids = set(
                editable_df.loc[
                    matches & family_mask,
                    "print_family_id",
                ]
                .dropna()
                .astype(str)
            )

            matched_family_ids.discard("")

            if matched_family_ids:
                matches = (
                    matches
                    | editable_df[
                        "print_family_id"
                    ].isin(matched_family_ids)
                )

        return editable_df[matches].copy()

    def _update_visible_part_columns(self):
        """Apply the checked Columns-menu choices to the Treeview."""
        visible_columns = [
            column
            for column in self.parts_tree["columns"]
            if self.parts_column_vars[column].get()
        ]
        show_images = self.parts_column_vars["image"].get()

        # Avoid a completely blank table if every option is unchecked.
        if not visible_columns and not show_images:
            self.parts_column_vars["part_num"].set(True)
            visible_columns = ["part_num"]

        self.parts_tree.configure(
            displaycolumns=visible_columns,
            show="tree headings" if show_images else "headings",
        )

        if (
            self.parts_sort_column is not None
            and self.parts_sort_column not in visible_columns
        ):
            self.parts_sort_column = None
            self.parts_sort_ascending = True

        if self.parts_df is not None:
            self._render_parts_table()

        self.status_var.set("Updated visible required-parts columns.")

    def _show_all_part_columns(self):
        for variable in self.parts_column_vars.values():
            variable.set(True)
        self._update_visible_part_columns()

    def _sort_parts_table(self, column):
        """Toggle sorting for a clicked required-parts column."""
        if self.parts_df is None:
            return

        if self.parts_sort_column == column:
            self.parts_sort_ascending = not self.parts_sort_ascending
        else:
            self.parts_sort_column = column
            self.parts_sort_ascending = True

        self._render_parts_table()
        direction = (
            "ascending"
            if self.parts_sort_ascending
            else "descending"
        )
        label = self.part_heading_labels.get(column, column)
        self.status_var.set(f"Sorted by {label} ({direction}).")

    def _update_parts_sort_headings(self):
        """Show an arrow on the active table heading."""
        for column, label in self.part_heading_labels.items():
            if column == self.parts_sort_column:
                arrow = " ▲" if self.parts_sort_ascending else " ▼"
            else:
                arrow = ""

            self.parts_tree.heading(
                column,
                text=f"{label}{arrow}",
            )

    def _sort_editable_parts(self, editable_df):
        """Sort official part rows using numeric or text-aware values."""
        if self.parts_sort_column is None:
            return editable_df.sort_values(
                by=[
                    "has_counterpart",
                    "counterpart_group_id",
                    "part_num",
                    "color_name",
                ],
                ascending=[False, True, True, True],
                kind="stable",
            ).reset_index(drop=True)

        column_map = {
            "bl_part_num": "bricklink_part_num",
            "bl_color_id": "bricklink_color_id",
            "required": "quantity_required",
            "have": "quantity_have",
            "variation": "variation_note",
        }
        source_column = column_map.get(
            self.parts_sort_column,
            self.parts_sort_column,
        )

        sorted_df = editable_df.copy()

        numeric_columns = {
            "color_id",
            "bricklink_color_id",
            "quantity_required",
            "quantity_have",
        }

        if source_column in numeric_columns:
            sorted_df["_primary_sort"] = pd.to_numeric(
                sorted_df[source_column],
                errors="coerce",
            ).fillna(0)
        else:
            sorted_df["_primary_sort"] = (
                sorted_df[source_column]
                .fillna("")
                .astype(str)
                .str.casefold()
            )

        sorted_df["_part_sort"] = (
            sorted_df["part_num"]
            .fillna("")
            .astype(str)
            .str.casefold()
        )
        sorted_df["_color_sort"] = (
            sorted_df["color_name"]
            .fillna("")
            .astype(str)
            .str.casefold()
        )

        return (
            sorted_df
            .sort_values(
                by=[
                    "_primary_sort",
                    "_part_sort",
                    "_color_sort",
                ],
                ascending=[
                    self.parts_sort_ascending,
                    True,
                    True,
                ],
                kind="stable",
                na_position="last",
            )
            .drop(
                columns=[
                    "_primary_sort",
                    "_part_sort",
                    "_color_sort",
                ]
            )
            .reset_index(drop=True)
        )
    # Preserve the selected overall sort, but tuck each printed child directly
    # beneath its same-color plain base.
        
    @staticmethod
    def _build_print_family_summaries(editable_df):
        """
        Calculate display-only totals for plain/printed part families.

        The returned summary values are used only to render synthetic Treeview
        parent rows. The original official inventory rows remain unchanged.
        """
        required_columns = {
            "print_family_id",
            "print_family_base_part_num",
            "print_family_role",
            "has_print_family",
            "part_num",
            "color_id",
            "color_name",
            "quantity_required",
            "quantity_have",
            "image_url",
        }

        if not required_columns.issubset(editable_df.columns):
            return {}

        family_rows_df = editable_df[
            editable_df["has_print_family"]
            .fillna(False)
            .astype(bool)
        ].copy()

        if family_rows_df.empty:
            return {}

        summaries = {}

        for family_id, group_df in family_rows_df.groupby(
            "print_family_id",
            sort=False,
        ):
            family_id = str(family_id).strip()

            if not family_id:
                continue

            first_row = group_df.iloc[0]

            base_part_num = str(
                first_row.get(
                    "print_family_base_part_num",
                    "",
                )
            )

            color_id = first_row.get("color_id")

            if pd.isna(color_id):
                color_id = ""
            else:
                color_id = int(color_id)

            color_name = first_row.get(
                "color_name",
                "",
            )

            if pd.isna(color_name):
                color_name = ""

            quantity_required = int(
                pd.to_numeric(
                    group_df["quantity_required"],
                    errors="coerce",
                )
                .fillna(0)
                .sum()
            )

            quantity_have = int(
                pd.to_numeric(
                    group_df["quantity_have"],
                    errors="coerce",
                )
                .fillna(0)
                .sum()
            )

            # Prefer the plain base image for the synthetic family summary.
            plain_rows_df = group_df[
                group_df["print_family_role"]
                == "plain_base"
            ]

            if plain_rows_df.empty:
                image_source = first_row
            else:
                image_source = plain_rows_df.iloc[0]

            image_url = image_source.get(
                "image_url",
                "",
            )

            if pd.isna(image_url):
                image_url = ""

            bricklink_base_part_num = image_source.get(
                "bricklink_part_num",
                "",
            )
            if pd.isna(bricklink_base_part_num):
                bricklink_base_part_num = ""

            bricklink_color_id = image_source.get("bricklink_color_id")
            if pd.isna(bricklink_color_id):
                bricklink_color_id = ""
            else:
                bricklink_color_id = int(bricklink_color_id)

            summaries[family_id] = {
                "base_part_num": base_part_num,
                "bricklink_base_part_num": str(bricklink_base_part_num),
                "bricklink_color_id": bricklink_color_id,
                "color_id": color_id,
                "color_name": str(color_name),
                "quantity_required": quantity_required,
                "quantity_have": quantity_have,
                "image_url": str(image_url).strip(),
            }

        return summaries

    @staticmethod
    def _build_print_family_row_values(summary):
        """Return a synthetic print-family row aligned to parts columns."""
        return (
            summary["bricklink_base_part_num"],
            summary["bricklink_color_id"],
            f"Base family {summary['base_part_num']}",
            "Plain and printed versions",
            summary["color_id"],
            summary["color_name"],
            summary["quantity_required"],
            summary["quantity_have"],
            "",
            "Print family total",
            "",
            "",
        )

    def _render_parts_table(self):
        self._clear_tree(self.parts_tree)
        self.part_row_keys = {}
        self.image_generation += 1
        current_image_generation = self.image_generation

        if self.parts_df is None:
            return

        current_have_df = combine_entry_csv(self.user_input_df)
        editable_df = self.parts_df.merge(
            current_have_df[
                ["part_num", "color_id", "quantity_have"]
            ],
            on=["part_num", "color_id"],
            how="left",
        )
        editable_df["quantity_have"] = (
            editable_df["quantity_have"].fillna(0).astype(int)
        )

        # Put the variation wording in the frame itself so the colour coding is
        # readable in the table, and so search and sort work on it like any
        # other column.
        substitution_labels = self._substitution_labels()
        variation_keys = [
            (
                str(part_num),
                self._color_key(color_id),
            )
            for part_num, color_id in zip(
                editable_df["part_num"],
                editable_df["color_id"],
            )
        ]
        editable_df["variation_tag"] = [
            substitution_labels.get(key, ("", ""))[0]
            for key in variation_keys
        ]
        editable_df["variation_note"] = [
            substitution_labels.get(key, ("", ""))[1]
            for key in variation_keys
        ]

        self.total_parts_count = len(editable_df)
        editable_df = self._filter_editable_parts(editable_df)
        self.visible_parts_count = len(editable_df)
        editable_df = self._sort_editable_parts(editable_df)
        self._update_parts_sort_headings()

        # One URL can appear in several part/color rows. Group row IDs by URL
        # so each unique thumbnail is downloaded only once.
        pending_images = {}
        show_images = self.parts_column_vars["image"].get()

        family_summaries = self._build_print_family_summaries(
            editable_df
        )

        family_parent_ids = {}

        for position, row in editable_df.iterrows():
            item_id = f"part_{position}"

            part_num = str(row["part_num"])

            color_id = (
                int(row["color_id"])
                if pd.notna(row["color_id"])
                else None
            )

            self.part_row_keys[item_id] = (
                part_num,
                color_id,
            )

            family_id = row.get(
                "print_family_id",
                "",
            )

            if pd.isna(family_id):
                family_id = ""

            family_id = str(family_id).strip()
            parent_item_id = ""

            # Create one synthetic parent the first time this family appears.
            if (
                family_id
                and family_id in family_summaries
            ):
                parent_item_id = family_parent_ids.get(
                    family_id,
                    "",
                )

                if not parent_item_id:
                    summary = family_summaries[family_id]

                    parent_item_id = (
                        f"print_family_{len(family_parent_ids)}"
                    )

                    family_parent_ids[family_id] = (
                        parent_item_id
                    )

                    family_image_url = (
                        summary["image_url"]
                        if show_images
                        else ""
                    )

                    family_thumbnail = (
                        self.thumbnail_cache.get(
                            family_image_url
                        )
                    )

                    self.parts_tree.insert(
                        "",
                        "end",
                        iid=parent_item_id,
                        open=True,
                        image=family_thumbnail or "",
                        values=self._build_print_family_row_values(
                            summary
                        ),
                    )

                    if (
                        family_image_url
                        and family_thumbnail is None
                    ):
                        pending_images.setdefault(
                            family_image_url,
                            [],
                        ).append(parent_item_id)

            display_part_num = (
                f"↳ {part_num}"
                if parent_item_id
                else part_num
            )

            image_url = (
                row.get("image_url", "")
                if show_images
                else ""
            )
            if pd.isna(image_url):
                image_url = ""
            image_url = str(image_url).strip()
            thumbnail = self.thumbnail_cache.get(image_url)

            substitution_tag = row.get("variation_tag", "") or ""

            self.parts_tree.insert(
                parent_item_id,
                "end",
                iid=item_id,
                image=thumbnail or "",
                tags=(substitution_tag,) if substitution_tag else (),
                values=(
                    self._display_bricklink_identifier(
                        row.get("bricklink_part_num")
                    ),
                    self._display_bricklink_identifier(
                        row.get("bricklink_color_id"),
                        numeric=True,
                    ),
                    display_part_num,
                    row.get("part_name", ""),
                    row.get("color_id", ""),
                    row.get("color_name", ""),
                    int(row.get("quantity_required", 0)),
                    int(row.get("quantity_have", 0)),
                    row.get("variation_note", "") or "",
                    row.get("counterpart", ""),
                    row.get("mold_variations", ""),
                    row.get("alternatives", ""),
                ),
            )

            if image_url and thumbnail is None:
                pending_images.setdefault(image_url, []).append(item_id)

        self._load_tree_images(
            self.parts_tree,
            pending_images,
            current_image_generation,
            "image_generation",
        )

    def _poll_image_queue(self):
        """
        Attach finished downloads to their widgets from the main thread.

        Tkinter is not thread safe: a widget call made from a download thread
        is silently discarded rather than raising, so every picture has to be
        applied here instead.
        """
        while True:
            try:
                apply_image = self.image_queue.get_nowait()
            except queue.Empty:
                break

            try:
                apply_image()
            except tk.TclError:
                # The table or dialog went away mid-download.
                continue

        try:
            self.root.after(150, self._poll_image_queue)
        except tk.TclError:
            # The app is shutting down.
            pass

    def _load_tree_images(
        self,
        tree,
        pending_images,
        image_generation,
        generation_attribute,
    ):
        """Download and resize thumbnails for a Treeview in the background."""
        if not pending_images:
            return

        def load():
            session = requests.Session()
            for image_url, item_ids in pending_images.items():
                # Stop an old loader when its table has been rendered again.
                if image_generation != getattr(
                    self,
                    generation_attribute,
                ):
                    return

                try:
                    response = session.get(image_url, timeout=10)
                    response.raise_for_status()
                    with Image.open(BytesIO(response.content)) as source:
                        thumbnail = source.convert("RGBA")
                        resampling = getattr(
                            Image,
                            "Resampling",
                            Image,
                        ).LANCZOS
                        thumbnail.thumbnail(
                            (64, 64),
                            resampling,
                        )
                        thumbnail = thumbnail.copy()
                except (
                    requests.RequestException,
                    OSError,
                    UnidentifiedImageError,
                ):
                    # A missing image should not prevent quantity entry.
                    continue

                self.image_queue.put(
                    lambda url=image_url, rows=item_ids, picture=thumbnail: (
                        self._apply_tree_thumbnail(
                            tree,
                            image_generation,
                            generation_attribute,
                            url,
                            rows,
                            picture,
                        )
                    )
                )

        threading.Thread(target=load, daemon=True).start()

    def _apply_tree_thumbnail(
        self,
        tree,
        image_generation,
        generation_attribute,
        image_url,
        item_ids,
        thumbnail,
    ):
        """Create Tk images on the main thread and attach them to table rows."""
        if image_generation != getattr(
            self,
            generation_attribute,
        ):
            return

        photo = self.thumbnail_cache.get(image_url)
        if photo is None:
            photo = ImageTk.PhotoImage(thumbnail)
            # Tkinter images disappear if Python drops the final reference.
            self.thumbnail_cache[image_url] = photo

        for item_id in item_ids:
            if tree.exists(item_id):
                tree.item(item_id, image=photo)

    def _load_widget_images(self, pending_images):
        """
        Download thumbnails for plain widgets, such as a dialog's labels.

        pending_images maps an image URL to the widgets that should display it.
        Shares self.thumbnail_cache with the tables, so a picture the parts
        table already fetched costs nothing here.
        """
        if not pending_images:
            return

        def load():
            session = requests.Session()
            for image_url, widgets in pending_images.items():
                if image_url not in self.thumbnail_cache:
                    try:
                        response = session.get(image_url, timeout=10)
                        response.raise_for_status()
                        with Image.open(BytesIO(response.content)) as source:
                            thumbnail = source.convert("RGBA")
                            resampling = getattr(
                                Image,
                                "Resampling",
                                Image,
                            ).LANCZOS
                            thumbnail.thumbnail((64, 64), resampling)
                            thumbnail = thumbnail.copy()
                    except (
                        requests.RequestException,
                        OSError,
                        UnidentifiedImageError,
                    ):
                        # A missing picture must not block the decision.
                        continue
                else:
                    thumbnail = None

                self.image_queue.put(
                    lambda url=image_url, targets=widgets, picture=thumbnail: (
                        self._apply_widget_thumbnail(
                            url,
                            targets,
                            picture,
                        )
                    )
                )

        threading.Thread(target=load, daemon=True).start()

    def _apply_widget_thumbnail(self, image_url, widgets, thumbnail):
        """Create the Tk image on the main thread and show it on each widget."""
        photo = self.thumbnail_cache.get(image_url)

        if photo is None:
            if thumbnail is None:
                return
            photo = ImageTk.PhotoImage(thumbnail)
            # Tkinter images disappear if Python drops the final reference.
            self.thumbnail_cache[image_url] = photo

        for widget in widgets:
            try:
                if widget.winfo_exists():
                    widget.configure(image=photo)
            except tk.TclError:
                # The dialog closed while the download was in flight.
                continue

    @staticmethod
    def _resolve_displayed_column(
        column_id,
        all_columns,
        display_columns,
    ):
        """Map Treeview's visible #n position back to its real column name."""
        if column_id == "#0":
            return "#0"

        try:
            display_index = int(column_id.removeprefix("#")) - 1
        except (TypeError, ValueError):
            return None

        if isinstance(display_columns, str):
            display_columns = (display_columns,)

        visible_columns = list(display_columns)
        if not visible_columns or visible_columns == ["#all"]:
            visible_columns = list(all_columns)

        if not 0 <= display_index < len(visible_columns):
            return None

        return visible_columns[display_index]

    def _begin_quantity_edit(self, event):
        """Open an editor when a real part's Have cell is double-clicked."""
        if self.busy:
            return self._show_busy_action_message("Editing Have quantities")
        if self.parts_df is None:
                return
        if self.workflow_stage == self.complete_stage:
            self.status_var.set(
                "This verification is complete. Reopen it from the report "
                "before editing quantities."
            )
            return

        item_id = self.parts_tree.identify_row(event.y)
        column_id = self.parts_tree.identify_column(event.x)

        clicked_column = self._resolve_displayed_column(
            column_id,
            self.parts_tree["columns"],
            self.parts_tree["displaycolumns"],
        )

        if (not item_id or clicked_column != "have"):
            return
        
        # Synthetic family totals are calculated and cannot be edited directly.
        if item_id not in self.part_row_keys:
            self.status_var.set(
                "The family total is calculated automatically. "
                "Edit one of the individual parts beneath it."
            )
            return

        bounds = self.parts_tree.bbox(item_id, "have")
        if not bounds:
            return

        x, y, width, height = bounds
        current_value = self.parts_tree.set(item_id, "have")
        editor = ttk.Entry(self.parts_tree)
        editor.insert(0, current_value)
        editor.select_range(0, "end")
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()

        is_family_member = bool(self.parts_tree.parent(item_id))

        def commit(_event=None):
            if not editor.winfo_exists():
                return "break"

            raw_value = editor.get().strip() or "0"
            try:
                quantity = int(raw_value)
                if quantity < 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    "Invalid quantity",
                    "Quantity must be a whole number of zero or more.",
                    parent=self.root,
                )
                editor.focus_set()
                editor.select_range(0, "end")
                return "break"

            part_num, color_id = self.part_row_keys[item_id]
            self._set_quantity(part_num, color_id, quantity)
            editor.destroy()

            self._render_parts_table()
            self._invalidate_report()
            self.status_var.set(
                f"Updated part {part_num} to quantity {quantity}."
            )
            return "break"

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind(
            "<Escape>",
            lambda _event: editor.destroy(),
        )

    def _set_quantity(self, part_num, color_id, quantity):
        current = combine_entry_csv(self.user_input_df)
        if current.empty:
            current = pd.DataFrame(
                columns=[
                    "part_num",
                    "color_id",
                    "bricklink_color_id",
                    "quantity_have",
                ]
            )

        current["part_num"] = current["part_num"].astype(str)
        current["color_id"] = pd.to_numeric(
            current["color_id"],
            errors="coerce",
        ).astype("Int64")

        if color_id is None:
            color_mask = current["color_id"].isna()
            color_value = pd.NA
        else:
            color_value = int(color_id)
            color_mask = current["color_id"] == color_value

        mask = (
            (current["part_num"] == str(part_num))
            & color_mask
        )
        quantity = int(quantity)
        quantity_changed = (
            not mask.any()
            or not current.loc[mask, "quantity_have"].eq(quantity).all()
        )

        if mask.any():
            current.loc[mask, "quantity_have"] = quantity
        else:
            new_row = pd.DataFrame(
                [{
                    "part_num": str(part_num),
                    "color_id": color_value,
                    "quantity_have": quantity,
                }]
            )
            current = pd.concat(
                [current, new_row],
                ignore_index=True,
            )

        self.user_input_df = current[
            [
                "part_num",
                "color_id",
                "bricklink_color_id",
                "quantity_have",
            ]
        ]
        if quantity_changed:
            self._mark_job_dirty()

    def prefill_csv(self):
        if self.parts_df is None:
            messagebox.showwarning(
                "Load a set first",
                "Load or resume a set before prefilling quantities.",
                parent=self.root,
            )
            return
        if self.workflow_stage == self.complete_stage:
            messagebox.showinfo(
                "Verification is complete",
                "Reopen the verification before importing new quantities.",
                parent=self.root,
            )
            return

        csv_path = filedialog.askopenfilename(
            parent=self.root,
            title="Choose a reviewed Brickognize CSV export",
            initialdir=BRICKOGNIZE_RESULTS_DIR,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not csv_path:
            return

        # Translating BrickLink part numbers can reach the API, so keep this
        # off the event loop.
        self._run_background(
            status=f"Importing {Path(csv_path).name}...",
            work=lambda: self._import_csv_data(csv_path),
            on_success=lambda result: self._finish_csv_import(
                csv_path,
                result,
            ),
        )

    def _import_csv_data(self, csv_path):
        content_hash = hashlib.sha256(Path(csv_path).read_bytes()).hexdigest()
        csv_have_df, summary = load_from_csv(
            csv_path,
            source_name=Path(csv_path).name,
        )
        proposals_df = find_variation_substitutions(
            csv_have_df,
            self.parts_df,
            self.relationships_df,
        )
        proposals_df = self._attach_proposal_images(proposals_df)
        return csv_have_df, summary, proposals_df, content_hash

    def _attach_proposal_images(self, proposals_df, parts_df=None):
        """
        Add best-available images for both sides of each variation proposal.

        Color-specific Rebrickable images are preferred. The color endpoint is
        called once per unique part and uses the existing 24-hour HTTP cache.
        When Rebrickable has no image for a legitimate part/color combination,
        the normal catalog picture remains as an explicitly labelled fallback.
        This method runs on the import/resume worker thread.
        """
        if proposals_df.empty:
            return proposals_df

        result_df = proposals_df.copy()

        set_images = {}
        source_parts_df = self.parts_df if parts_df is None else parts_df
        if (
            source_parts_df is not None
            and "image_url" in source_parts_df.columns
        ):
            for _, row in source_parts_df.iterrows():
                part_num = str(row["part_num"])
                url = row.get("image_url")

                if part_num not in set_images and pd.notna(url) and url:
                    set_images[part_num] = str(url)

        csv_images = {}
        wanted_part_nums = sorted(
            {str(value) for value in result_df["csv_part_num"]})

        try:
            lookup_df = get_parts_bulk_df(wanted_part_nums)
        except RebrickableAPIError:
            # A missing picture must not block the import.
            lookup_df = pd.DataFrame()

        if not lookup_df.empty and "image_url" in lookup_df.columns:
            for _, row in lookup_df.iterrows():
                url = row.get("image_url")

                if pd.notna(url) and url:
                    csv_images[str(row["part_num"])] = str(url)

        requested_colors = {}
        for _, row in result_df.iterrows():
            for part_column, color_column in (
                ("csv_part_num", "csv_color_id"),
                ("set_part_num", "set_color_id"),
            ):
                part_num = str(row[part_column])
                color_id = self._color_key(row[color_column])
                if color_id is not None:
                    requested_colors.setdefault(part_num, set()).add(color_id)

        color_specific_images = {}
        for part_num in sorted(requested_colors):
            try:
                colors_df = get_part_colors_df(part_num)
            except RebrickableAPIError:
                # A color-specific picture is optional; keep the fallback.
                continue

            if colors_df.empty or "image_url" not in colors_df.columns:
                continue

            for color_id in requested_colors[part_num]:
                matching_rows = colors_df[
                    colors_df["color_id"] == color_id
                ]
                for value in matching_rows["image_url"]:
                    if pd.notna(value) and str(value).strip():
                        color_specific_images[(part_num, color_id)] = (
                            str(value).strip()
                        )
                        break

        for prefix, part_column, color_column, fallback_images in (
            ("csv", "csv_part_num", "csv_color_id", csv_images),
            ("set", "set_part_num", "set_color_id", set_images),
        ):
            image_urls = []
            color_matched = []
            for _, row in result_df.iterrows():
                part_num = str(row[part_column])
                color_id = self._color_key(row[color_column])
                matching_url = color_specific_images.get(
                    (part_num, color_id),
                    "",
                )
                image_urls.append(
                    matching_url or fallback_images.get(part_num, "")
                )
                color_matched.append(bool(matching_url))

            result_df[f"{prefix}_image_url"] = image_urls
            result_df[f"{prefix}_image_color_matched"] = color_matched

        return result_df

    def _finish_csv_import(self, csv_path, result):
        if len(result) == 3:
            csv_have_df, summary, new_proposals_df = result
            content_hash = ""
        else:
            csv_have_df, summary, new_proposals_df, content_hash = result
        previous_proposals = (
            self.variation_proposals.copy()
            if self.variation_proposals is not None
            else pd.DataFrame()
        )
        current_baseline_df = self._restore_preallocation_baseline(
            previous_proposals
        )
        impact = self._csv_import_impact(current_baseline_df, csv_have_df)
        is_duplicate = bool(content_hash) and any(
            record.get("content_hash") == content_hash
            for record in getattr(self, "import_history", [])
        )
        has_existing_values = bool(
            pd.to_numeric(
                current_baseline_df.get("quantity_have", pd.Series(dtype=int)),
                errors="coerce",
            ).fillna(0).gt(0).any()
        )
        if has_existing_values or is_duplicate:
            import_mode = self._choose_csv_import_mode(
                csv_path=csv_path,
                summary=summary,
                impact=impact,
                is_duplicate=is_duplicate,
            )
        else:
            import_mode = self.import_mode_fill

        if import_mode == self.import_mode_cancel:
            self.status_var.set("CSV import cancelled; no quantities changed.")
            return

        if import_mode == self.import_mode_add:
            allocation_base_df = combine_entry_csv(
                current_baseline_df,
                csv_have_df,
            )
        elif import_mode == self.import_mode_fill:
            allocation_base_df = fill_blank_quantities(
                current_baseline_df,
                csv_have_df,
            )
        else:
            raise ValueError(f"Unknown CSV import mode: {import_mode}")

        contributions = self._csv_import_contributions(
            current_baseline_df,
            csv_have_df,
            import_mode,
        )
        proposals_df = self._merge_variation_proposals(
            previous_proposals,
            new_proposals_df,
        )
        proposals_df = self._attach_proposal_source_quantities(
            proposals_df,
            allocation_base_df,
        )
        target_shortages = self._variation_shortages_for_have(
            proposals_df,
            allocation_base_df,
        )
        allocated_df = pd.DataFrame(
            columns=[*proposals_df.columns, "quantity_allocated"]
        )
        if not proposals_df.empty:
            initial_allocations = {
                self._substitution_key(record):
                    self._record_allocated_quantity(record)
                for record in self.substitutions
            }
            allocated_df = self._review_variation_substitutions(
                proposals_df,
                initial_allocations=initial_allocations,
                target_shortages=target_shortages,
            )

        if allocated_df is None:
            self.status_var.set("CSV import cancelled; no quantities changed.")
            return

        try:
            replayed_have_df = apply_variation_substitutions(
                allocation_base_df,
                allocated_df,
                target_shortages=target_shortages,
            )
        except ValueError as exc:
            messagebox.showerror(
                "Could not allocate variations",
                str(exc),
                parent=self.root,
            )
            return

        # Keep the untouched import so a later change of mind can be replayed
        # from it rather than trying to reverse quantity transfers.
        self.import_have_df = allocation_base_df.copy()
        self.variation_proposals = proposals_df.copy()
        self.substitutions = []
        self.declined_substitutions = []
        self._record_substitution_choices(proposals_df, allocated_df)
        self._update_review_variations_button()
        self.user_input_df = replayed_have_df
        if not hasattr(self, "import_history"):
            self.import_history = []
        self.import_history.append({
            "source_name": Path(csv_path).name,
            "content_hash": content_hash,
            "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": import_mode,
            "contributions": contributions,
        })
        self._render_parts_table()
        self._invalidate_report()
        self._mark_job_dirty()
        self._show_import_summary(summary)

        allocated_quantity = (
            0
            if allocated_df.empty
            else int(allocated_df["quantity_allocated"].sum())
        )
        allocated_targets = len(allocated_df)
        mode_description = (
            "added as another scan batch"
            if import_mode == self.import_mode_add
            else "used to fill blank quantities"
        )
        message = (
            f"Imported {summary.total_quantity} parts across "
            f"{summary.distinct_combinations} part/color rows from "
            f"{Path(csv_path).name}; {mode_description}."
        )
        if allocated_quantity:
            message += (
                f" Allocated {allocated_quantity} variation part(s) across "
                f"{allocated_targets} required target(s)."
            )
        if not proposals_df.empty:
            message += " Use Review variation allocations to make changes."

        self.status_var.set(message)

    def _restore_preallocation_baseline(self, previous_proposals):
        """Undo prior variation transfers without touching unrelated rows."""
        current_df = combine_entry_csv(self.user_input_df)
        if (
            previous_proposals is None
            or previous_proposals.empty
            or self.import_have_df is None
        ):
            return current_df

        affected_keys = set()
        for _, row in previous_proposals.iterrows():
            affected_keys.add((
                str(row["csv_part_num"]),
                self._color_key(row["csv_color_id"]),
            ))
            affected_keys.add((
                str(row["set_part_num"]),
                self._color_key(row["set_color_id"]),
            ))

        def affected_mask(dataframe):
            return pd.Series([
                (
                    str(row["part_num"]),
                    self._color_key(row["color_id"]),
                ) in affected_keys
                for _, row in dataframe.iterrows()
            ], index=dataframe.index, dtype=bool)

        prior_baseline_df = combine_entry_csv(self.import_have_df)
        return combine_entry_csv(
            current_df[~affected_mask(current_df)],
            prior_baseline_df[affected_mask(prior_baseline_df)],
        )

    @staticmethod
    def _merge_variation_proposals(previous_df, incoming_df):
        """Keep earlier offers and add any newly imported variation offers."""
        frames = [
            frame.copy()
            for frame in (previous_df, incoming_df)
            if frame is not None and not frame.empty
        ]
        if not frames:
            return pd.DataFrame()

        result_df = pd.concat(frames, ignore_index=True)
        identity_columns = [
            "csv_part_num",
            "csv_color_id",
            "set_part_num",
            "set_color_id",
            "rel_type",
        ]
        available_columns = [
            column for column in identity_columns if column in result_df.columns
        ]
        return result_df.drop_duplicates(
            subset=available_columns,
            keep="last",
        ).reset_index(drop=True)

    def _csv_import_impact(self, current_df, incoming_df):
        """Summarize how an incoming batch overlaps the pre-import baseline."""
        current = combine_entry_csv(current_df)
        incoming = combine_entry_csv(incoming_df)
        current_lookup = {
            self._have_identity(row): int(row["quantity_have"])
            for _, row in current.iterrows()
        }
        new_rows = 0
        zero_rows = 0
        nonzero_rows = 0
        for _, row in incoming.iterrows():
            quantity = int(row["quantity_have"])
            if quantity <= 0:
                continue
            key = self._have_identity(row)
            if key not in current_lookup:
                new_rows += 1
            elif current_lookup[key] > 0:
                nonzero_rows += 1
            else:
                zero_rows += 1
        return {
            "new_rows": new_rows,
            "zero_rows": zero_rows,
            "nonzero_rows": nonzero_rows,
            "incoming_quantity": int(incoming["quantity_have"].sum())
                if not incoming.empty else 0,
        }

    def _choose_csv_import_mode(
            self,
            csv_path,
            summary,
            impact,
            is_duplicate):
        """Ask how an additional reviewed CSV should affect Have quantities."""
        result = {"mode": self.import_mode_cancel}
        dialog = tk.Toplevel(self.root)
        dialog.title("Import reviewed CSV")
        dialog.transient(self.root)
        dialog.resizable(False, False)

        body = [
            f"File: {Path(csv_path).name}",
            "",
            f"Incoming quantity: {impact['incoming_quantity']}",
            f"New part/color rows: {impact['new_rows']}",
            f"Zero Have values that can be filled: {impact['zero_rows']}",
            f"Existing nonzero Have values: {impact['nonzero_rows']}",
        ]
        if is_duplicate:
            body.extend([
                "",
                "Warning: this exact CSV content was imported before. Adding "
                "it again will count the same scan batch twice.",
            ])
        if summary.has_warnings:
            body.extend(["", "Import warnings:", *summary.as_lines()])

        ttk.Label(
            dialog,
            text="\n".join(body),
            justify="left",
            wraplength=620,
            padding=(18, 16, 18, 10),
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(
            dialog,
            text=(
                "Add treats this as another scan batch and increases existing "
                "values. Fill blanks preserves every nonzero Have value."
            ),
            justify="left",
            wraplength=620,
            padding=(18, 0, 18, 12),
        ).grid(row=1, column=0, sticky="ew")

        button_frame = ttk.Frame(dialog, padding=(18, 0, 18, 16))
        button_frame.grid(row=2, column=0, sticky="ew")

        def finish(mode):
            result["mode"] = mode
            dialog.destroy()

        add_button = ttk.Button(
            button_frame,
            text="Add as another scan batch",
            command=lambda: finish(self.import_mode_add),
        )
        add_button.pack(side="left")
        ttk.Button(
            button_frame,
            text="Fill blanks only",
            command=lambda: finish(self.import_mode_fill),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            button_frame,
            text="Cancel",
            command=lambda: finish(self.import_mode_cancel),
        ).pack(side="right")

        dialog.protocol(
            "WM_DELETE_WINDOW",
            lambda: finish(self.import_mode_cancel),
        )
        dialog.bind(
            "<Escape>",
            lambda _event: finish(self.import_mode_cancel),
        )
        dialog.grab_set()
        add_button.focus_set()
        dialog.wait_window()
        return result["mode"]

    def _csv_import_contributions(self, current_df, incoming_df, import_mode):
        """Return the exact raw quantities this accepted CSV contributed."""
        current_lookup = {
            self._have_identity(row): int(row["quantity_have"])
            for _, row in combine_entry_csv(current_df).iterrows()
        }
        contributions = []
        for _, row in combine_entry_csv(incoming_df).iterrows():
            quantity = int(row["quantity_have"])
            key = self._have_identity(row)
            if quantity <= 0:
                continue
            if (
                import_mode == self.import_mode_fill
                and current_lookup.get(key, 0) > 0
            ):
                continue
            contributions.append({
                "part_num": key[0],
                "color_id": key[1],
                "bricklink_color_id": key[2],
                "quantity": quantity,
            })
        return contributions

    def _have_identity(self, row):
        """Return the full inventory identity, including an unmapped BL color."""
        return (
            str(row["part_num"]),
            self._color_key(row["color_id"]),
            self._color_key(row.get("bricklink_color_id")),
        )

    def _variation_shortages_for_have(self, proposals_df, have_df):
        """Calculate target shortages directly from a complete import baseline."""
        have_lookup = {
            (str(row["part_num"]), self._color_key(row["color_id"])):
                int(row["quantity_have"])
            for _, row in combine_entry_csv(have_df).iterrows()
        }
        shortages = {}
        for _, row in proposals_df.iterrows():
            target_key = (
                str(row["set_part_num"]),
                self._color_key(row["set_color_id"]),
            )
            required_value = row.get("quantity_required")
            if required_value is None or pd.isna(required_value):
                required_value = self._required_quantity_for_key(target_key)
            shortages[target_key] = max(
                0,
                int(required_value or 0) - have_lookup.get(target_key, 0),
            )
        return shortages

    def _update_review_variations_button(self):
        """Offer the review button only when there is something to review."""
        has_proposals = (
            self.variation_proposals is not None
            and not self.variation_proposals.empty
        )
        self.review_variations_button.configure(
            state=(
                "normal"
                if (
                    has_proposals
                    and self.workflow_stage != self.complete_stage
                    and not self.busy
                )
                else "disabled"
            )
        )

    def review_variation_choices(self):
        """
        Reopen the variation review so allocation quantities can be changed.

        Replays the whole decision from the untouched import rather than
        undoing individual moves, which keeps repeated edits consistent.
        """
        if self.busy:
            return self._show_busy_action_message("Variation review")
        if self.workflow_stage == self.complete_stage:
            messagebox.showinfo(
                "Verification is complete",
                "Reopen the verification before changing variation choices.",
                parent=self.root,
            )
            return

        if (
            self.variation_proposals is None
            or self.variation_proposals.empty
            or self.import_have_df is None
        ):
            messagebox.showinfo(
                "Nothing to review",
                "Import a Brickognize CSV that contains a variation of a part "
                "this set needs, and the choices will be editable here.",
                parent=self.root,
            )
            return

        proposals_df = self.variation_proposals
        initial_allocations = {
            self._substitution_key(record):
                self._record_allocated_quantity(record)
            for record in self.substitutions
        }
        target_shortages = self._variation_target_shortages(
            proposals_df,
            baseline_have_df=self.import_have_df,
        )
        allocated_df = self._review_variation_substitutions(
            proposals_df,
            initial_allocations=initial_allocations,
            target_shortages=target_shortages,
        )

        if allocated_df is None:
            return

        try:
            replayed_have_df = apply_variation_substitutions(
                self.import_have_df,
                allocated_df,
                target_shortages=target_shortages,
            )
        except ValueError as exc:
            messagebox.showerror(
                "Could not allocate variations",
                str(exc),
                parent=self.root,
            )
            return

        self.substitutions = []
        self.declined_substitutions = []
        self._record_substitution_choices(proposals_df, allocated_df)
        self._apply_replayed_import(proposals_df, replayed_have_df)

        self._render_parts_table()
        self._invalidate_report()
        self._mark_job_dirty()
        allocated_quantity = (
            0
            if allocated_df.empty
            else int(allocated_df["quantity_allocated"].sum())
        )
        self.status_var.set(
            f"Variation allocations updated: {allocated_quantity} part(s) "
            f"allocated across {len(allocated_df)} target(s)."
        )

    def _apply_replayed_import(self, proposals_df, replayed_have_df):
        """
        Rewrite only the quantities a variation decision can affect.

        Both sides of every offer are reset and refilled from the replayed
        import, so a reversed decision moves its quantity back. Quantities for
        every other part are left exactly as the user left them, including any
        hand edits.
        """
        affected_keys = set()
        for _, row in proposals_df.iterrows():
            affected_keys.add(
                (str(row["csv_part_num"]), self._color_key(row["csv_color_id"]))
            )
            affected_keys.add(
                (str(row["set_part_num"]), self._color_key(row["set_color_id"]))
            )

        replayed_lookup = {}
        if replayed_have_df is not None and not replayed_have_df.empty:
            for _, row in replayed_have_df.iterrows():
                replayed_lookup[
                    (str(row["part_num"]), self._color_key(row["color_id"]))
                ] = int(row["quantity_have"])

        for part_num, color_id in affected_keys:
            self._set_quantity(
                part_num,
                color_id,
                replayed_lookup.get((part_num, color_id), 0),
            )

    def _variation_target_shortages(
            self,
            proposals_df,
            baseline_have_df=None):
        """Return allocation capacity after removing earlier transfers."""
        current_df = combine_entry_csv(self.user_input_df)
        current_lookup = {
            (str(row["part_num"]), self._color_key(row["color_id"])):
                int(row["quantity_have"])
            for _, row in current_df.iterrows()
        }

        # Current target quantities already include the allocations in force.
        # Remove those transfers before calculating what can be reallocated.
        for record in self.substitutions:
            target_key = (
                str(record.get("set_part_num", "")),
                self._color_key(record.get("set_color_id")),
            )
            current_lookup[target_key] = max(
                0,
                current_lookup.get(target_key, 0)
                - self._record_allocated_quantity(record),
            )

        base_df = pd.DataFrame([
            {
                "part_num": part_num,
                "color_id": color_id,
                "quantity_have": quantity,
            }
            for (part_num, color_id), quantity in current_lookup.items()
        ])
        if baseline_have_df is not None:
            base_df = fill_blank_quantities(base_df, baseline_have_df)

        base_lookup = {
            (str(row["part_num"]), self._color_key(row["color_id"])):
                int(row["quantity_have"])
            for _, row in combine_entry_csv(base_df).iterrows()
        }

        shortages = {}
        for _, row in proposals_df.iterrows():
            target_key = (
                str(row["set_part_num"]),
                self._color_key(row["set_color_id"]),
            )
            required_value = row.get("quantity_required")
            if required_value is None or pd.isna(required_value):
                required_value = self._required_quantity_for_key(target_key)
            required_quantity = int(required_value or 0)
            shortages[target_key] = max(
                0,
                required_quantity - base_lookup.get(target_key, 0),
            )

        return shortages

    def _attach_proposal_source_quantities(self, proposals_df, have_df):
        """Use the effective fill-blanks quantity for each shared source."""
        if proposals_df is None or proposals_df.empty:
            return proposals_df.copy()

        source_lookup = {
            (str(row["part_num"]), self._color_key(row["color_id"])):
                int(row["quantity_have"])
            for _, row in combine_entry_csv(have_df).iterrows()
        }
        result_df = proposals_df.copy()
        result_df["quantity_have"] = [
            source_lookup.get(
                (
                    str(row["csv_part_num"]),
                    self._color_key(row["csv_color_id"]),
                ),
                int(row["quantity_have"]),
            )
            for _, row in result_df.iterrows()
        ]
        return result_df

    def _required_quantity_for_key(self, target_key):
        """Read a target quantity for legacy proposals that did not save it."""
        if self.parts_df is None or self.parts_df.empty:
            return 0

        part_num, color_id = target_key
        part_mask = self.parts_df["part_num"].astype(str) == str(part_num)
        colors = pd.to_numeric(
            self.parts_df["color_id"],
            errors="coerce",
        ).astype("Int64")
        color_mask = colors.isna() if color_id is None else colors == color_id
        matching = self.parts_df[part_mask & color_mask]
        if matching.empty:
            return 0
        return int(pd.to_numeric(
            matching["quantity_required"],
            errors="coerce",
        ).fillna(0).sum())

    def _show_import_summary(self, summary):
        """Report what the import counted, skipped, and could not translate."""
        body = "\n".join(summary.as_lines())

        if summary.has_warnings:
            messagebox.showwarning(
                "Import finished with warnings",
                body,
                parent=self.root,
            )
        else:
            messagebox.showinfo(
                "Import summary",
                body,
                parent=self.root,
            )

    def _record_substitution_choices(self, proposals_df, accepted_df):
        """Remember exact allocations so rows and saved jobs can explain them."""
        accepted_allocations = {}
        if accepted_df is not None and not accepted_df.empty:
            accepted_allocations = {
                self._substitution_key(row): int(row["quantity_allocated"])
                for _, row in accepted_df.iterrows()
            }

        for _, row in proposals_df.iterrows():
            key = self._substitution_key(row)
            record = {
                "csv_part_num": str(row["csv_part_num"]),
                "csv_color_id": self._color_key(row["csv_color_id"]),
                "set_part_num": str(row["set_part_num"]),
                "set_color_id": self._color_key(row["set_color_id"]),
                "quantity_have": int(row["quantity_have"]),
                "quantity_allocated": accepted_allocations.get(key, 0),
                "rel_type": str(row["rel_type"]),
            }

            if record["quantity_allocated"] > 0:
                self.substitutions.append(record)
            else:
                self.declined_substitutions.append(record)

    @staticmethod
    def _color_key(color_id):
        """Normalize a color to a JSON-safe, comparable value."""
        if color_id is None or pd.isna(color_id):
            return None
        return int(color_id)

    def _substitution_key(self, record):
        """Return the stable source/target identity for an allocation row."""
        set_color_id = self._color_key(record.get("set_color_id"))
        return (
            str(record.get("csv_part_num", "")),
            self._color_key(record.get("csv_color_id", set_color_id)),
            str(record.get("set_part_num", "")),
            set_color_id,
        )

    @staticmethod
    def _record_allocated_quantity(record):
        """Read new allocations while remaining compatible with old jobs."""
        value = record.get("quantity_allocated")
        if value is None or pd.isna(value):
            value = record.get("quantity_have", 0)
        return max(0, int(value or 0))

    def _substitution_labels(self):
        """Map (part_num, color_id) to the tag and note for a marked row."""
        rel_type_names = {"M": "mold variation", "A": "alternative"}
        declined_by_target = {}
        for record in self.declined_substitutions:
            key = (record["set_part_num"], record["set_color_id"])
            declined_by_target.setdefault(key, []).append(
                f"{record['csv_part_num']} "
                f"({rel_type_names.get(record['rel_type'], 'variation')})"
            )

        accepted_by_target = {}
        for record in self.substitutions:
            key = (record["set_part_num"], record["set_color_id"])
            accepted_by_target.setdefault(key, []).append(
                f"{self._record_allocated_quantity(record)} x "
                f"{record['csv_part_num']} "
                f"({rel_type_names.get(record['rel_type'], 'variation')})"
            )

        labels = {}
        for key, descriptions in declined_by_target.items():
            labels[key] = (
                "variation_declined",
                f"Skipped {', '.join(descriptions)}; left on source row(s)",
            )

        # Any allocation wins over declined offers for the same target.
        for key, descriptions in accepted_by_target.items():
            labels[key] = (
                "substituted",
                f"Counting {', '.join(descriptions)} as this part",
            )

        return labels

    def _review_variation_substitutions(
            self,
            proposals_df,
            initial_allocations=None,
            target_shortages=None):
        """
        Ask how many imported variations to allocate to each required target.

        Allocations start at zero for a new import. Reopening restores the exact
        quantities already in force. Returns only rows with a positive
        quantity_allocated, or None if the user cancelled.
        """
        initial_allocations = initial_allocations or {}
        target_shortages = target_shortages or {}
        dialog = tk.Toplevel(self.root)
        dialog.title("Allocate variation quantities")
        dialog.transient(self.root)
        dialog.geometry("1080x640")
        dialog.minsize(820, 460)

        # Grid so the scrolling list absorbs the extra height without ever
        # squeezing the buttons off the bottom of the dialog.
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)

        ttk.Label(
            dialog,
            text="Imported parts that are variations of parts this set needs",
            style="Subheading.TLabel",
            padding=(12, 12, 12, 0),
        ).grid(row=0, column=0, sticky="ew")

        ttk.Label(
            dialog,
            text=(
                "The variation on hand is shown on the left and the part "
                "required by the set is shown on the right. Their color is "
                "already confirmed to match, so compare the shape. A catalog "
                "fallback is labelled when a color-specific image is not "
                "available. Enter whole-number quantities to allocate. "
                "Allocations cannot exceed either the shared source quantity "
                "or a required part's remaining shortage. Unallocated pieces "
                "stay on the original source row."
            ),
            wraplength=1040,
            padding=(12, 4, 12, 8),
        ).grid(row=1, column=0, sticky="ew")

        body = ttk.Frame(dialog)
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        canvas = tk.Canvas(body, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            body,
            orient="vertical",
            command=canvas.yview,
        )
        options_frame = ttk.Frame(canvas, padding=(12, 0))

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas_window = canvas.create_window(
            (0, 0),
            window=options_frame,
            anchor="nw",
        )

        def _resize_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _match_canvas_width(event):
            canvas.itemconfigure(canvas_window, width=event.width)

        options_frame.bind("<Configure>", _resize_scrollregion)
        canvas.bind("<Configure>", _match_canvas_width)

        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        def _on_mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")

        # Bind only while the pointer is over the list, so the main window
        # keeps its own scrolling once this dialog closes.
        canvas.bind(
            "<Enter>",
            lambda _event: canvas.bind_all("<MouseWheel>", _on_mousewheel),
        )
        canvas.bind(
            "<Leave>",
            lambda _event: canvas.unbind_all("<MouseWheel>"),
        )

        rel_type_names = {"M": "mold variation", "A": "alternative"}
        allocation_vars = []
        variables_by_source = {}
        source_status = {}
        pending_images = {}
        initial_source_remaining = {}
        initial_target_remaining = dict(target_shortages)
        previous_source_key = None

        for position, row in proposals_df.iterrows():
            source_key = (
                str(row["csv_part_num"]),
                self._color_key(row["csv_color_id"]),
            )
            target_key = (
                str(row["set_part_num"]),
                self._color_key(row["set_color_id"]),
            )
            available = int(row["quantity_have"])
            shortage = int(target_shortages.get(target_key, 0))
            required_value = row.get("quantity_required")
            if required_value is None or pd.isna(required_value):
                required_value = self._required_quantity_for_key(target_key)
            required_quantity = int(required_value or 0)

            if source_key not in initial_source_remaining:
                initial_source_remaining[source_key] = available

            requested = int(
                initial_allocations.get(self._substitution_key(row), 0) or 0
            )
            initial_quantity = min(
                max(0, requested),
                initial_source_remaining[source_key],
                initial_target_remaining.get(target_key, 0),
            )
            initial_source_remaining[source_key] -= initial_quantity
            initial_target_remaining[target_key] = max(
                0,
                initial_target_remaining.get(target_key, 0) - initial_quantity,
            )

            if source_key != previous_source_key:
                source_header = ttk.Frame(options_frame, padding=(8, 8))
                source_header.pack(fill="x", pady=(10, 2))
                ttk.Label(
                    source_header,
                    text=(
                        f"Shared source: {row['csv_part_num']} / "
                        f"{row['set_color_name']} — {available} available"
                    ),
                    style="Subheading.TLabel",
                ).pack(side="left")
                status_var = tk.StringVar()
                ttk.Label(
                    source_header,
                    textvariable=status_var,
                ).pack(side="right")
                source_status[source_key] = (available, status_var)
                previous_source_key = source_key

            variable = tk.StringVar(value=str(initial_quantity))
            allocation_vars.append(
                (position, source_key, target_key, variable)
            )
            variables_by_source.setdefault(source_key, []).append(variable)

            row_frame = ttk.Frame(options_frame)
            row_frame.pack(fill="x", anchor="w", pady=6)

            allocation_frame = ttk.Frame(row_frame, padding=(0, 8))
            allocation_frame.pack(side="left", padx=(0, 8))
            ttk.Label(
                allocation_frame,
                text="Allocate",
                style="Subheading.TLabel",
            ).pack()
            ttk.Spinbox(
                allocation_frame,
                from_=0,
                to=max(0, min(available, shortage)),
                textvariable=variable,
                width=5,
                justify="center",
            ).pack(pady=(4, 0))

            image_specs = (
                (
                    "VARIATION ON HAND",
                    "csv_image_url",
                    "csv_image_color_matched",
                    f"{row['csv_part_num']} x{int(row['quantity_have'])}",
                ),
                (
                    "REQUIRED SET PART",
                    "set_image_url",
                    "set_image_color_matched",
                    str(row["set_part_num"]),
                ),
            )
            for image_index, (
                role_label,
                url_column,
                matched_column,
                caption,
            ) in enumerate(image_specs):
                if image_index:
                    ttk.Label(
                        row_frame,
                        text="may replace\n\u2192",
                        width=12,
                        anchor="center",
                        justify="center",
                    ).pack(side="left", padx=4)

                image_card = ttk.Frame(
                    row_frame,
                    padding=6,
                    relief="solid",
                    borderwidth=1,
                )
                image_card.pack(side="left", padx=4)

                ttk.Label(
                    image_card,
                    text=role_label,
                    style="Subheading.TLabel",
                    anchor="center",
                ).pack(fill="x", pady=(0, 4))

                thumbnail_label = ttk.Label(
                    image_card,
                    text=caption,
                    compound="top",
                    width=18,
                    anchor="center",
                    justify="center",
                )
                thumbnail_label.pack()

                image_url = str(row.get(url_column, "") or "").strip()
                matched_value = row.get(matched_column, False)
                color_matched = (
                    bool(matched_value)
                    if pd.notna(matched_value)
                    else False
                )

                if not image_url:
                    image_note = "Image unavailable"
                elif color_matched:
                    image_note = "Color-specific image"
                else:
                    image_note = "Catalog image—color may differ"

                ttk.Label(
                    image_card,
                    text=image_note,
                    anchor="center",
                    justify="center",
                    wraplength=150,
                ).pack(fill="x", pady=(4, 0))

                if not image_url:
                    continue

                cached = self.thumbnail_cache.get(image_url)

                if cached is not None:
                    thumbnail_label.configure(image=cached)
                else:
                    pending_images.setdefault(
                        image_url,
                        [],
                    ).append(thumbnail_label)

            details_frame = ttk.Frame(row_frame, padding=(8, 4))
            details_frame.pack(side="left", fill="x", expand=True)
            ttk.Label(
                details_frame,
                text=f"Required color: {row['set_color_name']}",
                style="Subheading.TLabel",
                wraplength=260,
                justify="left",
            ).pack(fill="x")
            ttk.Label(
                details_frame,
                text="Color match confirmed",
            ).pack(fill="x", pady=(0, 6))
            ttk.Label(
                details_frame,
                text=(
                    f"Required: {required_quantity}  •  "
                    f"Still needed: {shortage}"
                ),
            ).pack(fill="x", pady=(0, 6))
            ttk.Label(
                details_frame,
                text=(
                    f"{row['set_part_name']}\n"
                    f"{rel_type_names.get(row['rel_type'], 'variation')}"
                ),
                wraplength=260,
                justify="left",
            ).pack(fill="x")

        def _read_whole_number(variable):
            value = variable.get().strip()
            if not value:
                return None
            try:
                quantity = int(value)
            except ValueError:
                return None
            if quantity < 0:
                return None
            return quantity

        def _update_source_status(source_key):
            available, status_var = source_status[source_key]
            values = [
                _read_whole_number(variable)
                for variable in variables_by_source[source_key]
            ]
            if any(value is None for value in values):
                status_var.set("Enter whole numbers")
                return
            allocated = sum(values)
            remaining = available - allocated
            if remaining < 0:
                status_var.set(
                    f"Allocated {allocated} / {available} — over by {-remaining}"
                )
            else:
                status_var.set(
                    f"Allocated {allocated} / {available} — {remaining} remains"
                )

        for source_key, variables in variables_by_source.items():
            for variable in variables:
                variable.trace_add(
                    "write",
                    lambda *_args, key=source_key:
                        _update_source_status(key),
                )
            _update_source_status(source_key)

        self._load_widget_images(pending_images)

        button_row = ttk.Frame(dialog, padding=12)
        button_row.grid(row=3, column=0, sticky="ew")

        # None means the user closed without answering, which must not be
        # mistaken for "skip everything" when reopening an earlier decision.
        decision = {"allocations": None}

        def clear_all():
            for _, _, _, variable in allocation_vars:
                variable.set("0")

        def apply_allocations():
            allocations = []
            source_totals = {}
            target_totals = {}

            for position, source_key, target_key, variable in allocation_vars:
                quantity = _read_whole_number(variable)
                if quantity is None:
                    messagebox.showwarning(
                        "Whole numbers required",
                        "Every allocation must be a whole number of zero or "
                        "greater.",
                        parent=dialog,
                    )
                    return

                source_totals[source_key] = (
                    source_totals.get(source_key, 0) + quantity
                )
                target_totals[target_key] = (
                    target_totals.get(target_key, 0) + quantity
                )
                if quantity > 0:
                    allocations.append((position, quantity))

            for source_key, allocated in source_totals.items():
                available = source_status[source_key][0]
                if allocated > available:
                    messagebox.showwarning(
                        "Source quantity exceeded",
                        f"{source_key[0]} / color {source_key[1]} has "
                        f"{available} available, but {allocated} was "
                        "allocated.",
                        parent=dialog,
                    )
                    return

            for target_key, allocated in target_totals.items():
                shortage = int(target_shortages.get(target_key, 0))
                if allocated > shortage:
                    messagebox.showwarning(
                        "Target shortage exceeded",
                        f"{target_key[0]} / color {target_key[1]} still needs "
                        f"{shortage}, but {allocated} was allocated.",
                        parent=dialog,
                    )
                    return

            decision["allocations"] = allocations
            dialog.destroy()

        def leave_all_unallocated():
            decision["allocations"] = []
            dialog.destroy()

        def cancel():
            decision["allocations"] = None
            dialog.destroy()

        ttk.Button(
            button_row,
            text="Apply allocations",
            command=apply_allocations,
        ).pack(side="right")
        ttk.Button(
            button_row,
            text="Leave all unallocated",
            command=leave_all_unallocated,
        ).pack(side="right", padx=(0, 8))
        ttk.Button(
            button_row,
            text="Cancel",
            command=cancel,
        ).pack(side="right", padx=(0, 8))
        ttk.Button(
            button_row,
            text="Clear allocations",
            command=clear_all,
        ).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.grab_set()
        self.root.wait_window(dialog)

        if decision["allocations"] is None:
            return None

        result_columns = list(proposals_df.columns)
        if "quantity_allocated" not in result_columns:
            result_columns.append("quantity_allocated")

        if not decision["allocations"]:
            return pd.DataFrame(columns=result_columns)

        positions = [position for position, _ in decision["allocations"]]
        allocated_df = proposals_df.loc[positions].copy()
        allocated_df["quantity_allocated"] = [
            quantity for _, quantity in decision["allocations"]
        ]
        return allocated_df[result_columns]

    # =========================================================================
    # Relationship details
    # =========================================================================
    def _show_selected_part_summary(self, _event=None):
        selection = self.parts_tree.selection()
        if not selection:
            return
        
        item_id = selection[0]

        if item_id not in self.part_row_keys:
            values = self.parts_tree.item(
                item_id,
                "values",
            )

            family_name = (
                values[0]
                if values
                else "Print family"
            )

            self.status_var.set(
                f"{family_name} is a display-only summary. "
                "Select one of its part rows to edit or fetch details."
            )
            return

        part_num, color_id = self.part_row_keys[item_id]
        matching_rows = self.parts_df[
            self.parts_df["part_num"].astype(str) == part_num
        ]
        if matching_rows.empty:
            return

        row = matching_rows.iloc[0]
        counterpart = row.get("counterpart") or "none"
        mold = row.get("mold_variations") or "none"
        alternatives = row.get("alternatives") or "none"

        summary = (
            f"Part {part_num} | Counterpart: {counterpart} | "
            f"Mold: {mold} | Alternatives: {alternatives}"
        )

        _tag, substitution_note = self._substitution_labels().get(
            (part_num, color_id),
            ("", ""),
        )
        if substitution_note:
            summary = f"{substitution_note} | {summary}"

        self.status_var.set(summary)

    def fetch_selected_relationship_details(self):
        selection = self.parts_tree.selection()
        if not selection:
            messagebox.showwarning(
                "Select a part",
                "Select a part in the required-parts table first.",
                parent=self.root,
            )
            return

        item_id = selection[0]

        if item_id not in self.part_row_keys:
            messagebox.showinfo(
                "Select an individual part",
                (
                    "The family row is a display-only total. "
                    "Select a plain or printed part beneath it."
                ),
                parent=self.root,
            )
            return

        part_num, color_id = self.part_row_keys[item_id]
        self._run_background(
            status=f"Fetching variation/alternative details for {part_num}...",
            work=lambda: self._load_selected_variation_details(
                part_num,
                color_id,
            ),
            on_success=lambda details_df: (
                self._render_relationship_details(
                    part_num,
                    details_df,
                )
            ),
        )

    def _load_selected_variation_details(self, part_num, color_id):
        """Fetch related-part details and attach confirmed BrickLink IDs."""
        details_df = get_selected_variation_details(
            part_num,
            self.relationships_df,
            selected_color_id=color_id,
        )
        return self._attach_confirmed_bricklink_catalog_mappings(details_df)

    @staticmethod
    def _attach_confirmed_bricklink_catalog_mappings(details_df):
        """Add catalog-safe BrickLink IDs without trusting assumed matches."""
        result_df = details_df.copy()
        result_df["bricklink_part_num"] = ""

        if result_df.empty or "related_part_num" not in result_df.columns:
            return result_df

        related_part_nums = (
            result_df["related_part_num"]
            .dropna()
            .astype(str)
            .str.strip()
        )
        related_part_nums = related_part_nums[
            related_part_nums.ne("")
        ].drop_duplicates().tolist()
        if not related_part_nums:
            return result_df

        mapping_df = resolve_rebrickable_part_nums(related_part_nums)
        confirmed_methods = {
            DIRECT_MATCH,
            BRICKLINK_MATCH,
            EXTERNAL_ID_MATCH,
            MANUAL_MATCH,
        }
        confirmed_df = mapping_df[
            mapping_df["resolved_via"].isin(confirmed_methods)
        ].copy()
        confirmed_df["bricklink_part_num"] = confirmed_df[
            "bricklink_part_num"
        ].fillna("").astype(str).str.strip()
        confirmed_df = confirmed_df[
            confirmed_df["bricklink_part_num"].ne("")
        ]
        mapping_lookup = dict(
            zip(
                confirmed_df["rebrickable_part_num"].astype(str),
                confirmed_df["bricklink_part_num"],
            )
        )
        result_df["bricklink_part_num"] = result_df[
            "related_part_num"
        ].apply(lambda value: mapping_lookup.get(str(value), ""))
        return result_df

    def _clear_relationship_details(self):
        """Clear related rows and any catalog-link selection state."""
        self._clear_tree(self.relationship_tree)
        self.relationship_row_bricklink_parts = {}
        self._update_bricklink_catalog_button()

    def _update_bricklink_catalog_button(self, _event=None):
        """Enable the catalog button only for a confirmed selected mapping."""
        if not hasattr(self, "open_bricklink_catalog_button"):
            return

        selected_part_num = ""
        selection = self.relationship_tree.selection()
        if selection:
            selected_part_num = self.relationship_row_bricklink_parts.get(
                selection[0],
                "",
            )

        self.open_bricklink_catalog_button.configure(
            state=(
                "normal"
                if selected_part_num and not self.busy
                else "disabled"
            )
        )

    def open_selected_relationship_in_bricklink(self):
        """Open the selected confirmed BrickLink part in the web catalog."""
        selection = self.relationship_tree.selection()
        selected_part_num = (
            self.relationship_row_bricklink_parts.get(selection[0], "")
            if selection
            else ""
        )
        if not selected_part_num:
            messagebox.showwarning(
                "BrickLink part unavailable",
                (
                    "This related part does not have a confirmed BrickLink "
                    "part number, so its catalog page cannot be opened "
                    "safely."
                ),
                parent=self.root,
            )
            return

        self._open_bricklink_catalog_part(selected_part_num)

    def _open_bricklink_catalog_part(self, part_num, parent=None):
        """Open one safely encoded BrickLink part page for any review UI."""
        part_num = str(part_num).strip()
        if not part_num:
            return False

        catalog_url = (
            "https://www.bricklink.com/v2/catalog/catalogitem.page?P="
            f"{quote(part_num, safe='')}"
        )
        try:
            opened = webbrowser.open_new_tab(catalog_url)
        except (OSError, webbrowser.Error) as exc:
            messagebox.showerror(
                "Could not open BrickLink",
                f"The BrickLink catalog page could not be opened:\n{exc}",
                parent=parent or self.root,
            )
            return False

        if opened is False:
            messagebox.showerror(
                "Could not open BrickLink",
                "No web browser accepted the BrickLink catalog link.",
                parent=parent or self.root,
            )
            return False

        self.status_var.set(
            f"Opened BrickLink catalog for part {part_num}."
        )
        return True

    def _render_relationship_details(self, part_num, details_df):
        self.relationship_image_generation += 1
        current_image_generation = self.relationship_image_generation
        self._clear_relationship_details()

        if details_df.empty:
            self.status_var.set(
                f"No mold variations or alternatives found for {part_num}."
            )
            return

        pending_images = {}

        for position, row in details_df.reset_index(drop=True).iterrows():
            relationship = (
                "Mold variation"
                if row.get("rel_type") == "M"
                else "Alternative"
            )

            same_color_value = row.get("same_color_available")

            if pd.isna(same_color_value):
                same_color_display = "Unknown"
            elif bool(same_color_value):
                same_color_display = "Yes"
            else:
                same_color_display = "No"

            available_colors = row.get(
                "available_color_names",
                "",
            )
            if pd.isna(available_colors):
                available_colors = ""

            available_color_ids = row.get(
                "available_color_ids",
                "",
            )
            if pd.isna(available_color_ids):
                available_color_ids = ""

            item_id = f"relationship_{position}"
            bricklink_part_num = row.get("bricklink_part_num", "")
            if pd.isna(bricklink_part_num):
                bricklink_part_num = ""
            bricklink_part_num = str(bricklink_part_num).strip()
            image_url = row.get("related_image_url", "")
            if pd.isna(image_url):
                image_url = ""
            image_url = str(image_url).strip()
            thumbnail = self.thumbnail_cache.get(image_url)

            self.relationship_tree.insert(
                "",
                "end",
                iid=item_id,
                image=thumbnail or "",
                values=(
                    relationship,
                    row.get("related_part_num", ""),
                    bricklink_part_num or "Not mapped",
                    row.get("related_part_name", ""),
                    same_color_display,
                    available_colors,
                    available_color_ids,
                ),
            )

            if bricklink_part_num:
                self.relationship_row_bricklink_parts[
                    item_id
                ] = bricklink_part_num

            if image_url and thumbnail is None:
                pending_images.setdefault(image_url, []).append(item_id)

        self._load_tree_images(
            self.relationship_tree,
            pending_images,
            current_image_generation,
            "relationship_image_generation",
        )
        self._update_bricklink_catalog_button()

        self.status_var.set(
            f"Loaded {len(details_df)} related parts for {part_num} "
            "with color information."
        )

    # =========================================================================
    # Verification report table controls
    # =========================================================================
    def _schedule_report_search(self, _event=None):
        """Debounce report searching independently of Required Parts."""
        if self.report_search_after_id is not None:
            self.root.after_cancel(self.report_search_after_id)

        self.report_search_after_id = self.root.after(
            250,
            self._apply_report_search,
        )

    def _apply_report_search(self):
        self.report_search_after_id = None
        if self.report_df is None:
            return

        self._render_report()
        query = self.report_search_var.get().strip()
        if query:
            self.status_var.set(
                f'Search "{query}": showing {self.visible_report_count} '
                f"of {self.total_report_count} report rows."
            )
        else:
            self.status_var.set(
                f"Showing all {self.total_report_count} report rows."
            )

    def _clear_report_search(self):
        self.report_search_var.set("")
        self._apply_report_search()

    def _begin_report_remark_edit(self, event):
        """Edit the XML remark for a missing report row."""
        if self.report_df is None:
            return
        if self.workflow_stage == self.complete_stage:
            self.status_var.set(
                "This verification is complete. Reopen it before editing "
                "Bag / XML Remarks."
            )
            return

        item_id = self.report_tree.identify_row(event.y)
        column_id = self.report_tree.identify_column(event.x)
        clicked_column = self._resolve_displayed_column(
            column_id,
            self.report_tree["columns"],
            self.report_tree["displaycolumns"],
        )
        if not item_id or clicked_column != "xml_remark":
            return

        row_key = self.report_row_keys.get(item_id)
        if row_key is None:
            return

        part_num, color_id, status = row_key
        if status != "Missing":
            self.status_var.set(
                "Bag / XML Remark can be edited only for missing parts."
            )
            return

        bounds = self.report_tree.bbox(item_id, "xml_remark")
        if not bounds:
            return

        x, y, width, height = bounds
        current_value = self.report_tree.set(item_id, "xml_remark")
        editor = ttk.Entry(self.report_tree)
        editor.insert(0, current_value)
        editor.select_range(0, "end")
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()

        def commit(_event=None):
            if not editor.winfo_exists():
                return "break"

            remark = editor.get().strip()
            editor.destroy()
            changed = self._set_wanted_list_remark(
                part_num,
                color_id,
                remark,
            )
            self._render_report()
            if changed:
                action = "Cleared" if not remark else "Updated"
                self.status_var.set(
                    f"{action} XML remark for part {part_num}."
                )
            return "break"

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)

        def cancel(_event=None):
            if editor.winfo_exists():
                editor.unbind("<FocusOut>")
                editor.destroy()
            return "break"

        editor.bind("<Escape>", cancel)

    def _selected_missing_report_keys(self):
        """Return distinct selected Missing-row keys from the visible report."""
        if not hasattr(self, "report_tree"):
            return []

        selected_keys = []
        seen = set()
        for item_id in self.report_tree.selection():
            row_key = self.report_row_keys.get(item_id)
            if row_key is None:
                continue
            part_num, color_id, status = row_key
            key = (part_num, color_id)
            if status != "Missing" or key in seen:
                continue
            seen.add(key)
            selected_keys.append(key)
        return selected_keys

    def _update_bulk_remark_button(self):
        if not hasattr(self, "bulk_remark_button"):
            return
        can_assign = (
            self.report_df is not None
            and self.workflow_stage == self.report_review_stage
            and not self.busy
            and bool(self._selected_missing_report_keys())
        )
        self.bulk_remark_button.configure(
            state="normal" if can_assign else "disabled"
        )

    def assign_bulk_report_remark(self):
        """Assign one Bag/XML Remark to every selected Missing row."""
        if self.report_df is None:
            return
        if self.workflow_stage == self.complete_stage:
            self.status_var.set(
                "This verification is complete. Reopen it before assigning "
                "Bag / XML Remarks."
            )
            return

        selected_keys = self._selected_missing_report_keys()
        if not selected_keys:
            messagebox.showwarning(
                "Select missing parts",
                (
                    "Select one or more Missing rows first. Use Ctrl or Shift "
                    "to select several rows."
                ),
                parent=self.root,
            )
            return

        current_remarks = set()
        for part_num, color_id in selected_keys:
            matching_rows = self._part_color_mask(
                self.report_df,
                part_num,
                color_id,
            )
            values = self.report_df.loc[
                matching_rows,
                "wanted_list_remark",
            ] if "wanted_list_remark" in self.report_df.columns else []
            for value in values:
                current_remarks.add(self._display_text(value).strip())
        initial_value = (
            next(iter(current_remarks))
            if len(current_remarks) == 1
            else ""
        )

        remark = simpledialog.askstring(
            "Assign Bag / XML Remark",
            (
                f"Apply one remark to {len(selected_keys)} selected missing "
                "part row(s).\n\nLeave the field blank to clear their remarks."
            ),
            initialvalue=initial_value,
            parent=self.root,
        )
        if remark is None:
            return

        clean_remark = remark.strip()
        changed_count = sum(
            self._set_wanted_list_remark(
                part_num,
                color_id,
                clean_remark,
            )
            for part_num, color_id in selected_keys
        )
        self._render_report()

        if changed_count:
            action = "Cleared" if not clean_remark else "Assigned"
            self.status_var.set(
                f"{action} Bag / XML Remark for {changed_count} missing "
                "part row(s)."
            )
        else:
            self.status_var.set("The selected rows already had that remark.")

    @staticmethod
    def _part_color_mask(dataframe, part_num, color_id):
        """Match a report row using the canonical internal key."""
        part_mask = (
            dataframe["part_num"]
            .astype("string")
            .fillna("")
            .eq(str(part_num))
        )
        colors = pd.to_numeric(
            dataframe["color_id"],
            errors="coerce",
        ).astype("Int64")
        color_mask = colors.isna() if color_id is None else colors.eq(color_id)
        return (part_mask & color_mask).fillna(False)

    def _set_wanted_list_remark(self, part_num, color_id, remark):
        """Persist one remark and synchronize both unfiltered report sources."""
        key = (str(part_num), self._color_key(color_id))
        clean_remark = str(remark or "").strip()
        remark_lookup = {}

        if self.wanted_list_remarks_df is not None:
            for _, saved_row in self.wanted_list_remarks_df.iterrows():
                saved_part_num = saved_row.get("part_num")
                saved_remark = saved_row.get("wanted_list_remark")
                if (
                    saved_part_num is None
                    or pd.isna(saved_part_num)
                    or saved_remark is None
                    or pd.isna(saved_remark)
                ):
                    continue

                saved_key = (
                    str(saved_part_num),
                    self._color_key(saved_row.get("color_id")),
                )
                saved_text = str(saved_remark).strip()
                if saved_text:
                    remark_lookup[saved_key] = saved_text

        previous_remark = remark_lookup.get(key, "")
        if clean_remark:
            remark_lookup[key] = clean_remark
        else:
            remark_lookup.pop(key, None)

        self.wanted_list_remarks_df = pd.DataFrame(
            [
                {
                    "part_num": saved_part_num,
                    "color_id": saved_color_id,
                    "wanted_list_remark": saved_remark,
                }
                for (
                    saved_part_num,
                    saved_color_id,
                ), saved_remark in remark_lookup.items()
            ],
            columns=self.wanted_list_remark_columns,
        )
        if not self.wanted_list_remarks_df.empty:
            self.wanted_list_remarks_df["color_id"] = pd.to_numeric(
                self.wanted_list_remarks_df["color_id"],
                errors="coerce",
            ).astype("Int64")

        for attribute_name in ("report_df", "wanted_list_df"):
            dataframe = getattr(self, attribute_name)
            if dataframe is None or dataframe.empty:
                continue
            if "wanted_list_remark" not in dataframe.columns:
                dataframe["wanted_list_remark"] = ""
            matching_rows = self._part_color_mask(
                dataframe,
                key[0],
                key[1],
            )
            dataframe.loc[matching_rows, "wanted_list_remark"] = clean_remark

        changed = previous_remark != clean_remark
        if changed:
            self._mark_job_dirty()
        return changed

    def _filter_report(self, report_df):
        """Return report rows matching the case-insensitive search query."""
        query = self.report_search_var.get().strip().casefold()
        if not query:
            return report_df.copy()

        search_columns = [
            "status",
            "bricklink_part_num",
            "bricklink_color_id",
            "part_num",
            "part_name",
            "color_id",
            "color_name",
            "quantity_required",
            "quantity_have",
            "quantity_missing",
            "quantity_extra",
            "removal_status",
            "wanted_list_remark",
            "variation_note",
        ]
        matches = pd.Series(
            False,
            index=report_df.index,
            dtype=bool,
        )

        for column in search_columns:
            if column not in report_df.columns:
                continue

            values = (
                report_df[column]
                .astype("string")
                .fillna("")
                .str.casefold()
            )
            matches = matches | values.str.contains(
                query,
                regex=False,
                na=False,
            )

        display_notes = (
            report_df.apply(self._build_report_note, axis=1)
            .astype("string")
            .fillna("")
            .str.casefold()
        )
        matches = matches | display_notes.str.contains(
            query,
            regex=False,
            na=False,
        )

        return report_df[matches].copy()

    def _update_visible_report_columns(self):
        """Apply the report Columns-menu choices to its Treeview."""
        visible_columns = [
            column
            for column in self.report_tree["columns"]
            if self.report_column_vars[column].get()
        ]
        show_images = self.report_column_vars["image"].get()

        if not visible_columns and not show_images:
            self.report_column_vars["status"].set(True)
            visible_columns = ["status"]

        self.report_tree.configure(
            displaycolumns=visible_columns,
            show="tree headings" if show_images else "headings",
        )

        if (
            self.report_sort_column is not None
            and self.report_sort_column not in visible_columns
        ):
            self.report_sort_column = None
            self.report_sort_ascending = True

        if self.report_df is not None:
            self._render_report()

        self.status_var.set("Updated visible report columns.")

    def _show_all_report_columns(self):
        for variable in self.report_column_vars.values():
            variable.set(True)
        self._update_visible_report_columns()

    def _sort_report_table(self, column):
        """Toggle sorting for a clicked report column."""
        if self.report_df is None:
            return

        if self.report_sort_column == column:
            self.report_sort_ascending = not self.report_sort_ascending
        else:
            self.report_sort_column = column
            self.report_sort_ascending = True

        self._render_report()
        direction = (
            "ascending"
            if self.report_sort_ascending
            else "descending"
        )
        label = self.report_heading_labels.get(column, column)
        self.status_var.set(f"Sorted report by {label} ({direction}).")

    def _update_report_sort_headings(self):
        """Show an arrow on the active report heading."""
        for column, label in self.report_heading_labels.items():
            if column == self.report_sort_column:
                arrow = " ▲" if self.report_sort_ascending else " ▼"
            else:
                arrow = ""

            self.report_tree.heading(
                column,
                text=f"{label}{arrow}",
            )

    def _sort_report_rows(self, report_df):
        """Sort a display-only report copy with deterministic tie-breakers."""
        if self.report_sort_column is None:
            return report_df.copy().reset_index(drop=True)

        column_map = {
            "bl_part_num": "bricklink_part_num",
            "bl_color_id": "bricklink_color_id",
            "color": "color_name",
            "required": "quantity_required",
            "have": "quantity_have",
            "missing": "quantity_missing",
            "extra": "quantity_extra",
            "removal": "removal_status",
            "xml_remark": "wanted_list_remark",
        }
        source_column = column_map.get(
            self.report_sort_column,
            self.report_sort_column,
        )

        sorted_df = report_df.copy()
        numeric_columns = {
            "color_id",
            "bricklink_color_id",
            "quantity_required",
            "quantity_have",
            "quantity_missing",
            "quantity_extra",
        }
        if self.report_sort_column == "note":
            sorted_df["_primary_sort"] = (
                sorted_df.apply(self._build_report_note, axis=1)
                .astype(str)
                .str.casefold()
            )
        elif source_column in numeric_columns:
            sorted_df["_primary_sort"] = pd.to_numeric(
                sorted_df[source_column],
                errors="coerce",
            )
        else:
            sorted_df["_primary_sort"] = (
                sorted_df[source_column]
                .fillna("")
                .astype(str)
                .str.casefold()
            )

        sorted_df["_part_sort"] = (
            sorted_df["part_num"]
            .fillna("")
            .astype(str)
            .str.casefold()
        )
        sorted_df["_color_id_sort"] = pd.to_numeric(
            sorted_df["color_id"],
            errors="coerce",
        )

        return (
            sorted_df
            .sort_values(
                by=[
                    "_primary_sort",
                    "_part_sort",
                    "_color_id_sort",
                ],
                ascending=[
                    self.report_sort_ascending,
                    True,
                    True,
                ],
                kind="stable",
                na_position="last",
            )
            .drop(
                columns=[
                    "_primary_sort",
                    "_part_sort",
                    "_color_id_sort",
                ]
            )
            .reset_index(drop=True)
        )

    @staticmethod
    def _build_report_note(row):
        notes = []
        variation_note = row.get("variation_note", "")

        if pd.notna(variation_note) and variation_note:
            notes.append(str(variation_note))

        quantity_missing = pd.to_numeric(
            row.get("quantity_missing", 0),
            errors="coerce",
        )
        if pd.notna(quantity_missing) and quantity_missing > 0:
            missing_fields = []
            bricklink_part_num = row.get("bricklink_part_num")
            has_selected_part_num = (
                bricklink_part_num is not None
                and not pd.isna(bricklink_part_num)
                and bool(str(bricklink_part_num).strip())
            )
            candidates = parse_bricklink_candidates(
                row.get("bricklink_part_candidates", "")
            )
            has_ambiguous_part_mapping = (
                not has_selected_part_num and len(candidates) > 1
            )
            if has_ambiguous_part_mapping:
                notes.append(
                    "Multiple BrickLink IDs found — review required: "
                    + ", ".join(candidates)
                )

            if (
                not has_ambiguous_part_mapping
                and not has_selected_part_num
            ):
                missing_fields.append("part number")

            bricklink_color_id = row.get("bricklink_color_id")
            if (
                bricklink_color_id is None
                or pd.isna(bricklink_color_id)
                or not str(bricklink_color_id).strip()
            ):
                missing_fields.append("color ID")

            if missing_fields:
                field_names = " and ".join(missing_fields)
                notes.append(
                    f"Excluded from XML: BrickLink {field_names} unavailable"
                )

        return " | ".join(notes)

    def _invalidate_report(self):
        """Clear a report made stale by changed verification inputs."""
        self._close_removal_checklist()
        if self.report_search_after_id is not None:
            self.root.after_cancel(self.report_search_after_id)
            self.report_search_after_id = None

        self.report_df = None
        self.wanted_list_df = None
        self.workflow_stage = self.parts_entry_stage
        self.main_table_notebook.select(self.parts_tab)
        self.report_search_var.set("")
        self.report_sort_column = None
        self.report_sort_ascending = True
        self._render_report()
        self._update_command_states()

    # =========================================================================
    # Save, compare, and export
    # =========================================================================
    def _enrich_not_in_set_parts(
        self,
        comparison_df,
        saved_metadata_df=None,
    ):
        """Reuse or fetch catalog details for parts absent from the set."""
        result_df = comparison_df.copy()
        empty_metadata_df = pd.DataFrame(
            columns=self.extra_part_metadata_columns
        )

        if result_df.empty or "status" not in result_df.columns:
            return result_df, empty_metadata_df

        not_in_set_mask = result_df["status"].eq("Not in set")
        if not not_in_set_mask.any():
            return result_df, empty_metadata_df

        def clean_text(value):
            if value is None or pd.isna(value):
                return ""
            return str(value).strip()

        current_keys = []
        for row_index in result_df.index[not_in_set_mask]:
            key = (
                clean_text(result_df.at[row_index, "part_num"]),
                self._color_key(result_df.at[row_index, "color_id"]),
            )
            if key[0] and key not in current_keys:
                current_keys.append(key)

        saved_lookup = {}
        if saved_metadata_df is not None and not saved_metadata_df.empty:
            saved_df = saved_metadata_df.copy()
            for column in self.extra_part_metadata_columns:
                if column not in saved_df.columns:
                    saved_df[column] = pd.NA

            for _, metadata_row in saved_df.iterrows():
                key = (
                    clean_text(metadata_row.get("part_num")),
                    self._color_key(metadata_row.get("color_id")),
                )
                if key in current_keys:
                    saved_lookup[key] = {
                        "part_name": clean_text(
                            metadata_row.get("part_name")
                        ),
                        "color_name": clean_text(
                            metadata_row.get("color_name")
                        ),
                        "image_url": clean_text(
                            metadata_row.get("image_url")
                        ),
                    }

        missing_keys = [
            key for key in current_keys if key not in saved_lookup
        ]
        missing_part_nums = sorted({key[0] for key in missing_keys})
        lookup_errors = (
            RebrickableAPIError,
            ValueError,
            requests.RequestException,
            OSError,
        )

        part_names = {}
        bulk_lookup_succeeded = True
        if missing_part_nums:
            try:
                part_details_df = get_parts_bulk_df(missing_part_nums)
            except lookup_errors:
                # Do not persist a transient failure as a permanent blank.
                bulk_lookup_succeeded = False
                part_details_df = pd.DataFrame()

            if (
                not part_details_df.empty
                and {"part_num", "part_name"}.issubset(
                    part_details_df.columns
                )
            ):
                for _, detail_row in part_details_df.iterrows():
                    part_num = clean_text(detail_row.get("part_num"))
                    part_name = clean_text(detail_row.get("part_name"))
                    if part_num and part_name:
                        part_names[part_num] = part_name

        requested_colors = {}
        for part_num, color_id in missing_keys:
            if color_id is not None:
                requested_colors.setdefault(part_num, set()).add(color_id)

        exact_color_details = {}
        successful_color_lookups = set()
        for part_num in sorted(requested_colors):
            try:
                colors_df = get_part_colors_df(part_num)
            except lookup_errors:
                continue

            successful_color_lookups.add(part_num)
            if colors_df.empty or "color_id" not in colors_df.columns:
                continue

            for _, color_row in colors_df.iterrows():
                color_id = self._color_key(color_row.get("color_id"))
                if color_id not in requested_colors[part_num]:
                    continue

                key = (part_num, color_id)
                details = exact_color_details.setdefault(
                    key,
                    {"color_name": "", "image_url": ""},
                )
                color_name = clean_text(color_row.get("color_name"))
                image_url = clean_text(color_row.get("image_url"))
                if color_name and not details["color_name"]:
                    details["color_name"] = color_name
                if image_url and not details["image_url"]:
                    details["image_url"] = image_url

        fetched_lookup = {}
        for part_num, color_id in missing_keys:
            color_details = exact_color_details.get(
                (part_num, color_id),
                {},
            )
            fetched_lookup[(part_num, color_id)] = {
                "part_name": part_names.get(part_num, ""),
                "color_name": color_details.get("color_name", ""),
                "image_url": color_details.get("image_url", ""),
            }

        display_lookup = {**saved_lookup, **fetched_lookup}
        for row_index in result_df.index[not_in_set_mask]:
            key = (
                clean_text(result_df.at[row_index, "part_num"]),
                self._color_key(result_df.at[row_index, "color_id"]),
            )
            details = display_lookup.get(key, {})
            for column in ("part_name", "color_name", "image_url"):
                current_value = result_df.at[row_index, column]
                if not clean_text(current_value):
                    result_df.at[row_index, column] = details.get(column, "")

        metadata_rows = []
        for part_num, color_id in current_keys:
            key = (part_num, color_id)
            if key in saved_lookup:
                details = saved_lookup[key]
            elif (
                bulk_lookup_succeeded
                and (
                    color_id is None
                    or part_num in successful_color_lookups
                )
            ):
                details = fetched_lookup[key]
            else:
                # Retry incomplete API lookups on the next comparison.
                continue

            metadata_rows.append({
                "part_num": part_num,
                "color_id": color_id,
                "part_name": details["part_name"],
                "color_name": details["color_name"],
                "image_url": details["image_url"],
            })

        metadata_df = pd.DataFrame(
            metadata_rows,
            columns=self.extra_part_metadata_columns,
        )
        if not metadata_df.empty:
            metadata_df["color_id"] = pd.to_numeric(
                metadata_df["color_id"],
                errors="coerce",
            ).astype("Int64")

        return result_df, metadata_df

    def _build_comparison_result(
        self,
        required_parts_df,
        user_have_df,
        relationships_df,
        saved_metadata_df,
        saved_remarks_df,
    ):
        """Build and enrich comparison data away from Tk's event loop."""
        comparison_df = compare_parts(required_parts_df, user_have_df)
        comparison_df, metadata_df = self._enrich_not_in_set_parts(
            comparison_df,
            saved_metadata_df,
        )
        # Rows that came from the CSV rather than the set inventory still need
        # BrickLink values. This lookup uses the local mapping cache only.
        comparison_df = fill_missing_bricklink_ids(comparison_df)
        comparison_df = self._attach_wanted_list_remarks(
            comparison_df,
            saved_remarks_df,
        )
        report_df = attach_variation_surplus_notes(
            build_full_report(comparison_df),
            required_parts_df,
            relationships_df,
        )
        return comparison_df, report_df, metadata_df

    @classmethod
    def _attach_wanted_list_remarks(cls, comparison_df, remarks_df):
        """Attach saved operator remarks by the internal part/color key."""
        result_df = comparison_df.drop(
            columns=["wanted_list_remark"],
            errors="ignore",
        ).copy()

        if remarks_df is None or remarks_df.empty:
            result_df["wanted_list_remark"] = ""
            return result_df

        clean_remarks_df = remarks_df.copy()
        for column in cls.wanted_list_remark_columns:
            if column not in clean_remarks_df.columns:
                clean_remarks_df[column] = pd.NA

        clean_remarks_df = clean_remarks_df[
            list(cls.wanted_list_remark_columns)
        ]
        clean_remarks_df["part_num"] = (
            clean_remarks_df["part_num"]
            .astype("string")
            .fillna("")
            .str.strip()
        )
        clean_remarks_df["color_id"] = pd.to_numeric(
            clean_remarks_df["color_id"],
            errors="coerce",
        ).astype("Int64")
        clean_remarks_df["wanted_list_remark"] = (
            clean_remarks_df["wanted_list_remark"]
            .astype("string")
            .fillna("")
            .str.strip()
        )
        clean_remarks_df = (
            clean_remarks_df[
                clean_remarks_df["part_num"].ne("")
                & clean_remarks_df["wanted_list_remark"].ne("")
            ]
            .drop_duplicates(
                subset=["part_num", "color_id"],
                keep="last",
            )
        )

        result_df["part_num"] = result_df["part_num"].astype(str)
        result_df["color_id"] = pd.to_numeric(
            result_df["color_id"],
            errors="coerce",
        ).astype("Int64")
        result_df = result_df.merge(
            clean_remarks_df,
            on=["part_num", "color_id"],
            how="left",
        )
        result_df["wanted_list_remark"] = (
            result_df["wanted_list_remark"]
            .astype("string")
            .fillna("")
        )
        if "status" in result_df.columns:
            result_df.loc[
                result_df["status"].ne("Missing"),
                "wanted_list_remark",
            ] = ""
        return result_df

    @classmethod
    def _removal_rows(cls, report_df):
        """Return surplus rows that the operator must remove from the table."""
        if (
            report_df is None
            or report_df.empty
            or "status" not in report_df.columns
        ):
            return pd.DataFrame()

        quantities = pd.to_numeric(
            report_df.get(
                "quantity_extra",
                pd.Series(0, index=report_df.index),
            ),
            errors="coerce",
        ).fillna(0)
        mask = report_df["status"].isin(cls.removal_statuses) & quantities.gt(0)
        return report_df.loc[mask].copy()

    @classmethod
    def _attach_removal_states(cls, report_df, removal_states_df):
        """Reattach only decisions for the same part, color, and extra quantity."""
        result_df = report_df.drop(
            columns=["removal_status"],
            errors="ignore",
        ).copy()
        result_df["removal_status"] = ""
        eligible_rows = cls._removal_rows(result_df)
        if eligible_rows.empty:
            return result_df

        result_df.loc[eligible_rows.index, "removal_status"] = "Pending"
        if removal_states_df is None or removal_states_df.empty:
            return result_df

        clean_states_df = removal_states_df.copy()
        for column in cls.removal_state_columns:
            if column not in clean_states_df.columns:
                clean_states_df[column] = pd.NA
        clean_states_df = clean_states_df[list(cls.removal_state_columns)]
        clean_states_df["part_num"] = (
            clean_states_df["part_num"]
            .astype("string")
            .fillna("")
            .str.strip()
        )
        clean_states_df["color_id"] = pd.to_numeric(
            clean_states_df["color_id"],
            errors="coerce",
        ).astype("Int64")
        clean_states_df["quantity_to_remove"] = pd.to_numeric(
            clean_states_df["quantity_to_remove"],
            errors="coerce",
        ).astype("Int64")
        clean_states_df["removed"] = clean_states_df["removed"].map(
            lambda value: value is True
            or str(value).strip().casefold() in {"true", "1", "yes"}
        )
        clean_states_df = (
            clean_states_df[clean_states_df["part_num"].ne("")]
            .drop_duplicates(
                subset=["part_num", "color_id"],
                keep="last",
            )
            .rename(columns={
                "quantity_to_remove": "_saved_removal_quantity",
                "removed": "_saved_removed",
            })
        )

        result_df["part_num"] = result_df["part_num"].astype(str)
        result_df["color_id"] = pd.to_numeric(
            result_df["color_id"],
            errors="coerce",
        ).astype("Int64")
        result_df = result_df.merge(
            clean_states_df,
            on=["part_num", "color_id"],
            how="left",
        )
        current_quantity = pd.to_numeric(
            result_df["quantity_extra"],
            errors="coerce",
        ).fillna(0)
        saved_quantity = pd.to_numeric(
            result_df["_saved_removal_quantity"],
            errors="coerce",
        )
        restored_mask = (
            result_df["status"].isin(cls.removal_statuses)
            & current_quantity.gt(0)
            & current_quantity.eq(saved_quantity)
            & result_df["_saved_removed"].fillna(False).astype(bool)
        )
        result_df.loc[restored_mask, "removal_status"] = "Removed"
        return result_df.drop(
            columns=["_saved_removal_quantity", "_saved_removed"],
            errors="ignore",
        )

    def _sync_removal_states_from_report(self):
        """Keep a compact, saveable checklist matching the current report."""
        removal_rows = self._removal_rows(self.report_df)
        records = []
        for _, row in removal_rows.iterrows():
            records.append({
                "part_num": str(row.get("part_num", "")),
                "color_id": self._color_key(row.get("color_id")),
                "quantity_to_remove": int(
                    pd.to_numeric(
                        row.get("quantity_extra", 0),
                        errors="coerce",
                    )
                    or 0
                ),
                "removed": str(row.get("removal_status", "")) == "Removed",
            })

        self.removal_states_df = pd.DataFrame(
            records,
            columns=self.removal_state_columns,
        )
        if not self.removal_states_df.empty:
            self.removal_states_df["color_id"] = pd.to_numeric(
                self.removal_states_df["color_id"],
                errors="coerce",
            ).astype("Int64")
            self.removal_states_df["quantity_to_remove"] = pd.to_numeric(
                self.removal_states_df["quantity_to_remove"],
                errors="coerce",
            ).astype("Int64")

    def _pending_removal_count(self):
        removal_rows = self._removal_rows(self.report_df)
        if removal_rows.empty:
            return 0
        if "removal_status" not in removal_rows.columns:
            return len(removal_rows)
        return int(removal_rows["removal_status"].ne("Removed").sum())

    def _set_removal_state(self, part_num, color_id, removed):
        """Mark one current surplus row removed or pending."""
        if self.report_df is None or self.workflow_stage == self.complete_stage:
            return False

        matching_rows = self._part_color_mask(
            self.report_df,
            str(part_num),
            self._color_key(color_id),
        )
        quantities = pd.to_numeric(
            self.report_df.get(
                "quantity_extra",
                pd.Series(0, index=self.report_df.index),
            ),
            errors="coerce",
        ).fillna(0)
        eligible_rows = (
            matching_rows
            & self.report_df["status"].isin(self.removal_statuses)
            & quantities.gt(0)
        )
        if not eligible_rows.any():
            return False

        new_status = "Removed" if removed else "Pending"
        if "removal_status" not in self.report_df.columns:
            self.report_df["removal_status"] = ""
        previous_statuses = self.report_df.loc[
            eligible_rows,
            "removal_status",
        ]
        if previous_statuses.eq(new_status).all():
            return False

        self.report_df.loc[eligible_rows, "removal_status"] = new_status
        self._sync_removal_states_from_report()
        self._mark_job_dirty()
        return True

    def open_removal_checklist(self):
        """Show every surplus row without removing it from the audit report."""
        removal_rows = self._removal_rows(self.report_df)
        if removal_rows.empty:
            messagebox.showinfo(
                "No parts to remove",
                "The current report has no Extra or Not in set parts.",
                parent=self.root,
            )
            return

        if (
            self.removal_window is not None
            and self.removal_window.winfo_exists()
        ):
            self._render_removal_checklist()
            self.removal_window.lift()
            self.removal_window.focus_set()
            return

        window = tk.Toplevel(self.root)
        self.removal_window = window
        self.removal_status_var = tk.StringVar()
        window.title("Extra Parts Removal Checklist")
        window.transient(self.root)
        window.geometry("1080x560")
        window.minsize(760, 400)
        window.protocol("WM_DELETE_WINDOW", self._close_removal_checklist)

        content = ttk.Frame(window, padding=12)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)

        ttk.Label(
            content,
            text="Remove these surplus parts from the verification table.",
            style="Subheading.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            content,
            text=(
                "Select a row and mark it Removed after the physical parts "
                "are off the table. Rows stay visible for the audit trail."
            ),
        ).grid(row=1, column=0, sticky="w", pady=(3, 10))

        tree_frame = ttk.Frame(content)
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = (
            "state",
            "bl_part_num",
            "part_num",
            "part_name",
            "color",
            "quantity",
            "report_status",
        )
        self.removal_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="tree headings",
            selectmode="browse",
            style="Parts.Treeview",
        )
        headings = {
            "state": "Removal",
            "bl_part_num": "BL Part Number",
            "part_num": "RB Part Number",
            "part_name": "Name",
            "color": "Color",
            "quantity": "Quantity",
            "report_status": "Report Status",
        }
        widths = {
            "state": 120,
            "bl_part_num": 120,
            "part_num": 120,
            "part_name": 300,
            "color": 150,
            "quantity": 85,
            "report_status": 110,
        }
        self.removal_tree.heading("#0", text="Image", anchor="center")
        self.removal_tree.column(
            "#0",
            width=82,
            minwidth=82,
            stretch=False,
            anchor="center",
        )
        for column in columns:
            anchor = "center" if column in {
                "state",
                "bl_part_num",
                "part_num",
                "quantity",
                "report_status",
            } else "w"
            self.removal_tree.heading(
                column,
                text=headings[column],
                anchor=anchor,
            )
            self.removal_tree.column(
                column,
                width=widths[column],
                minwidth=70,
                stretch=column in {"part_name", "color"},
                anchor=anchor,
            )
        self.removal_tree.tag_configure("removed", foreground="#666666")

        vertical = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.removal_tree.yview,
        )
        horizontal = ttk.Scrollbar(
            tree_frame,
            orient="horizontal",
            command=self.removal_tree.xview,
        )
        self.removal_tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        self.removal_tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self._bind_fast_horizontal_scroll(self.removal_tree)
        self.removal_tree.bind(
            "<<TreeviewSelect>>",
            lambda _event: self._update_removal_checklist_buttons(),
        )
        self.removal_tree.bind(
            "<Double-1>",
            self._toggle_selected_removal,
        )

        footer = ttk.Frame(content)
        footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            textvariable=self.removal_status_var,
        ).grid(row=0, column=0, sticky="w")
        self.removal_pending_button = ttk.Button(
            footer,
            text="Mark Pending",
            command=lambda: self._set_selected_removal_state(False),
        )
        self.removal_pending_button.grid(row=0, column=1, padx=(8, 0))
        self.removal_mark_button = ttk.Button(
            footer,
            text="Mark Removed",
            command=lambda: self._set_selected_removal_state(True),
        )
        self.removal_mark_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(
            footer,
            text="Close",
            command=self._close_removal_checklist,
        ).grid(row=0, column=3, padx=(8, 0))

        window.bind(
            "<Escape>",
            lambda _event: self._close_removal_checklist(),
        )
        self._render_removal_checklist()
        window.focus_set()

    def _close_removal_checklist(self):
        self.removal_image_generation += 1
        if (
            self.removal_window is not None
            and self.removal_window.winfo_exists()
        ):
            self.removal_window.destroy()
        self.removal_window = None
        self.removal_tree = None
        self.removal_status_var = None
        self.removal_row_keys = {}
        self.removal_mark_button = None
        self.removal_pending_button = None

    def _render_removal_checklist(self):
        if (
            self.removal_window is None
            or not self.removal_window.winfo_exists()
            or self.removal_tree is None
        ):
            return

        self.removal_image_generation += 1
        current_generation = self.removal_image_generation
        self._clear_tree(self.removal_tree)
        self.removal_row_keys = {}
        removal_rows = self._removal_rows(self.report_df)
        if "removal_status" not in removal_rows.columns:
            removal_rows["removal_status"] = "Pending"
        removal_rows["_removed_sort"] = removal_rows[
            "removal_status"
        ].eq("Removed")
        removal_rows = removal_rows.sort_values(
            by=["_removed_sort", "part_num", "color_id"],
            ascending=[True, True, True],
            kind="stable",
        )

        pending_images = {}
        first_pending_item = None
        for position, (_, row) in enumerate(removal_rows.iterrows()):
            item_id = f"removal_{position}"
            removed = str(row.get("removal_status", "")) == "Removed"
            image_url = self._display_text(row.get("image_url")).strip()
            thumbnail = self.thumbnail_cache.get(image_url)
            self.removal_tree.insert(
                "",
                "end",
                iid=item_id,
                image=thumbnail or "",
                text="",
                tags=("removed",) if removed else (),
                values=(
                    "[x] Removed" if removed else "[ ] Pending",
                    self._display_bricklink_identifier(
                        row.get("bricklink_part_num")
                    ),
                    self._display_text(row.get("part_num")),
                    self._display_text(row.get("part_name")),
                    self._display_text(row.get("color_name")),
                    self._display_number(row.get("quantity_extra")),
                    self._display_text(row.get("status")),
                ),
            )
            self.removal_row_keys[item_id] = (
                str(row.get("part_num", "")),
                self._color_key(row.get("color_id")),
            )
            if not removed and first_pending_item is None:
                first_pending_item = item_id
            if image_url and thumbnail is None:
                pending_images.setdefault(image_url, []).append(item_id)

        total_count = len(removal_rows)
        pending_count = self._pending_removal_count()
        removed_count = total_count - pending_count
        status_text = (
            f"{removed_count} of {total_count} surplus row(s) marked Removed"
        )
        if self.workflow_stage == self.complete_stage:
            status_text += " — completed verification is read-only"
        self.removal_status_var.set(status_text)

        children = self.removal_tree.get_children()
        selected_item = first_pending_item or (children[0] if children else None)
        if selected_item:
            self.removal_tree.selection_set(selected_item)
            self.removal_tree.focus(selected_item)

        self._load_tree_images(
            self.removal_tree,
            pending_images,
            current_generation,
            "removal_image_generation",
        )
        self._update_removal_checklist_buttons()
        self._update_command_states()

    def _update_removal_checklist_buttons(self):
        if self.removal_tree is None:
            return
        selection = self.removal_tree.selection()
        current_state = (
            self.removal_tree.set(selection[0], "state")
            if selection
            else ""
        )
        editable = self.workflow_stage != self.complete_stage and bool(selection)
        self.removal_mark_button.configure(
            state=(
                "normal"
                if editable and current_state != "[x] Removed"
                else "disabled"
            )
        )
        self.removal_pending_button.configure(
            state=(
                "normal"
                if editable and current_state == "[x] Removed"
                else "disabled"
            )
        )

    def _set_selected_removal_state(self, removed):
        if self.removal_tree is None:
            return
        selection = self.removal_tree.selection()
        if not selection:
            return
        row_key = self.removal_row_keys.get(selection[0])
        if row_key is None:
            return

        if self._set_removal_state(*row_key, removed):
            self._render_report()
            self._render_removal_checklist()
            action = "Removed" if removed else "Pending"
            self.status_var.set(
                f"Marked part {row_key[0]} as {action}. Save Progress to "
                "keep this checklist update."
            )

    def _toggle_selected_removal(self, event=None):
        if self.workflow_stage == self.complete_stage:
            return "break"
        if event is not None:
            item_id = self.removal_tree.identify_row(event.y)
            if item_id:
                self.removal_tree.selection_set(item_id)
                self.removal_tree.focus(item_id)
        selection = self.removal_tree.selection()
        if not selection:
            return "break"
        is_removed = self.removal_tree.set(
            selection[0],
            "state",
        ) == "[x] Removed"
        self._set_selected_removal_state(not is_removed)
        return "break"

    def _mark_job_dirty(self):
        """Remember that active verification data changed."""
        self.job_dirty = True
        self._update_command_states()

    def _confirm_leave_current_job(self, parent=None):
        """Offer save/discard/cancel before replacing an edited active job."""
        if self.parts_df is None or not self.job_dirty:
            return True

        choice = messagebox.askyesnocancel(
            "Unsaved verification changes",
            (
                f"Save changes to set {self.set_num} before continuing?\n\n"
                "Yes: save and continue\n"
                "No: continue without saving\n"
                "Cancel: stay on the current set"
            ),
            parent=parent or self.root,
        )
        if choice is None:
            return False
        if choice:
            return self.save_progress(show_confirmation=False)
        return True

    def _request_exit(self):
        if self.busy:
            messagebox.showinfo(
                "Operation in progress",
                "Wait for the current operation to finish before closing.",
                parent=self.root,
            )
            return
        if self._confirm_leave_current_job():
            self.root.destroy()

    def save_progress(self, show_confirmation=True):
        if self.parts_df is None:
            messagebox.showwarning(
                "Nothing to save",
                "Load or resume a set first.",
                parent=self.root,
            )
            return False

        if not self.job_id:
            self.job_id = make_job_id(self.set_num)

        try:
            self._save_active_job()
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror(
                "Could not save progress",
                str(exc),
                parent=self.root,
            )
            return False

        self.job_dirty = False
        self._refresh_saved_jobs()
        self.status_var.set(f"Saved job {self.job_id}.")
        if show_confirmation:
            messagebox.showinfo(
                "Progress saved",
                "You can close the app and resume this job later.",
                parent=self.root,
            )
        return True

    def _save_active_job(self):
        """Write the active stage and any exact report snapshots to disk."""
        status = (
            "complete"
            if self.workflow_stage == self.complete_stage
            else "in_progress"
        )
        return save_job(
            JOBS_DIR,
            self.job_id,
            self.set_num,
            combine_entry_csv(self.user_input_df),
            status=status,
            substitutions=self.substitutions,
            declined_substitutions=self.declined_substitutions,
            variation_proposals_df=self.variation_proposals,
            import_have_df=self.import_have_df,
            extra_part_metadata_df=self.extra_part_metadata_df,
            wanted_list_remarks_df=self.wanted_list_remarks_df,
            removal_states_df=self.removal_states_df,
            import_history=getattr(self, "import_history", []),
            workflow_stage=self.workflow_stage,
            report_snapshot_df=self.report_df,
            wanted_list_snapshot_df=self.wanted_list_df,
        )

    def compare_parts(self):
        if self.parts_df is None:
            messagebox.showwarning(
                "Load a set first",
                "Load or resume a set before comparing quantities.",
                parent=self.root,
            )
            return
        if self.workflow_stage != self.parts_entry_stage:
            return

        required_parts_df = self.parts_df.copy()
        user_have_df = combine_entry_csv(self.user_input_df)
        relationships_df = self.relationships_df.copy()
        saved_metadata_df = self.extra_part_metadata_df.copy()
        saved_remarks_df = self.wanted_list_remarks_df.copy()

        self._run_background(
            status="Comparing parts and loading extra-part details...",
            work=lambda: self._build_comparison_result(
                required_parts_df,
                user_have_df,
                relationships_df,
                saved_metadata_df,
                saved_remarks_df,
            ),
            on_success=self._finish_comparison,
        )

    def _finish_comparison(self, result):
        comparison_df, report_df, metadata_df = result
        # Keep the complete comparison as the XML source. The Wanted List
        # selector will retain only rows with a remaining missing quantity.
        self.wanted_list_df = comparison_df.copy()
        self.report_df = self._attach_removal_states(
            report_df,
            self.removal_states_df,
        )
        self._sync_removal_states_from_report()
        self.extra_part_metadata_df = metadata_df
        self.workflow_stage = self.report_review_stage
        self._render_report()
        self.main_table_notebook.select(self.report_tab)
        self.job_dirty = True

        if not self.job_id:
            self.job_id = make_job_id(self.set_num)

        try:
            self._save_active_job()
            self.job_dirty = False
            self._refresh_saved_jobs()
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showwarning(
                "Comparison complete, save failed",
                str(exc),
                parent=self.root,
            )

        self._update_command_states()

        if self.report_df.empty:
            self.status_var.set(
                "Comparison found no discrepancies. Review the report, then "
                "click Finish Verification."
            )
            messagebox.showinfo(
                "Comparison ready",
                (
                    "Everything matches. Review the report, then click "
                    "Finish Verification to mark the job complete."
                ),
                parent=self.root,
            )
        else:
            self.status_var.set(
                f"Comparison complete: {len(self.report_df)} rows "
                "need attention. Review them, then click Finish Verification."
            )

        if not self._removal_rows(self.report_df).empty:
            self.open_removal_checklist()

    def _handle_report_stage_action(self):
        if self.workflow_stage == self.complete_stage:
            self.reopen_verification()
        else:
            self.finish_verification()

    def finish_verification(self):
        """Mark a reviewed report complete and persist its exact snapshot."""
        if (
            self.workflow_stage != self.report_review_stage
            or self.report_df is None
            or self.busy
        ):
            return

        pending_removals = self._pending_removal_count()
        if pending_removals:
            messagebox.showwarning(
                "Removal checklist incomplete",
                (
                    f"{pending_removals} surplus row(s) are still Pending.\n\n"
                    "Remove those parts from the verification table and mark "
                    "each row Removed before finishing."
                ),
                parent=self.root,
            )
            self.open_removal_checklist()
            return

        if not messagebox.askyesno(
            "Finish verification?",
            (
                "Mark this verification complete?\n\n"
                "The report will become read-only. You can reopen it later "
                "if corrections are needed."
            ),
            parent=self.root,
        ):
            return

        self.workflow_stage = self.complete_stage
        try:
            self._save_active_job()
        except (OSError, TypeError, ValueError) as exc:
            self.workflow_stage = self.report_review_stage
            self._update_command_states()
            messagebox.showerror(
                "Could not finish verification",
                str(exc),
                parent=self.root,
            )
            return

        self.job_dirty = False
        self._refresh_saved_jobs()
        self._update_review_variations_button()
        self._update_command_states()
        self._render_removal_checklist()
        self.status_var.set("Verification finished and saved as Complete.")

    def reopen_verification(self):
        """Return a completed report to editable review state."""
        if self.workflow_stage != self.complete_stage or self.busy:
            return

        if not messagebox.askyesno(
            "Reopen verification?",
            (
                "Reopen this completed verification for corrections?\n\n"
                "Its saved-job status will return to In Progress."
            ),
            parent=self.root,
        ):
            return

        self.workflow_stage = self.report_review_stage
        try:
            self._save_active_job()
        except (OSError, TypeError, ValueError) as exc:
            self.workflow_stage = self.complete_stage
            self._update_command_states()
            messagebox.showerror(
                "Could not reopen verification",
                str(exc),
                parent=self.root,
            )
            return

        self.job_dirty = False
        self._refresh_saved_jobs()
        self._update_review_variations_button()
        self._update_command_states()
        self._render_removal_checklist()
        self.status_var.set(
            "Verification reopened. The saved job is now In Progress."
        )

    def _render_report(self):
        self._clear_tree(self.report_tree)
        self.report_row_keys = {}
        self._update_bulk_remark_button()

        self.report_image_generation += 1
        current_image_generation = self.report_image_generation

        if self.report_df is None:
            self.total_report_count = 0
            self.visible_report_count = 0
            self._update_report_sort_headings()
            return

        report_display_df = self.report_df.copy()
        self.total_report_count = len(report_display_df)
        report_display_df = self._filter_report(report_display_df)
        self.visible_report_count = len(report_display_df)
        report_display_df = self._sort_report_rows(report_display_df)
        self._update_report_sort_headings()

        if report_display_df.empty:
            return

        pending_images = {}
        show_images = self.report_column_vars["image"].get()

        for position, row in report_display_df.iterrows():
            item_id = f"report_{position}"

            color_id = row.get("color_id")
            color_name = row.get("color_name", "")

            if pd.isna(color_id):
                color_id = ""
            else:
                color_id = int(color_id)

            if pd.isna(color_name):
                color_name = ""

            notes = self._build_report_note(row)
            wanted_list_remark = row.get("wanted_list_remark", "")
            if pd.isna(wanted_list_remark):
                wanted_list_remark = ""
            wanted_list_remark = str(wanted_list_remark)
            removal_status = row.get("removal_status", "")
            if pd.isna(removal_status):
                removal_status = ""
            removal_status = str(removal_status)
            image_url = row.get("image_url", "") if show_images else ""
            if pd.isna(image_url):
                image_url = ""
            image_url = str(image_url).strip()
            thumbnail = self.thumbnail_cache.get(image_url)

            self.report_tree.insert(
                "",
                "end",
                iid=item_id,
                image=thumbnail or "",
                text="",
                tags=("removed",) if removal_status == "Removed" else (),
                values=(
                    row.get("status", ""),
                    self._display_bricklink_identifier(
                        row.get("bricklink_part_num")
                    ),
                    self._display_bricklink_identifier(
                        row.get("bricklink_color_id"),
                        numeric=True,
                    ),
                    row.get("part_num", ""),
                    row.get("part_name", ""),
                    color_id,
                    color_name,
                    self._display_number(
                        row.get("quantity_required")
                    ),
                    self._display_number(row.get("quantity_have")),
                    self._display_number(
                        row.get("quantity_missing")
                    ),
                    self._display_number(row.get("quantity_extra")),
                    removal_status,
                    wanted_list_remark,
                    notes,
                ),
            )
            self.report_row_keys[item_id] = (
                str(row.get("part_num", "")),
                self._color_key(row.get("color_id")),
                str(row.get("status", "")),
            )

            if image_url and thumbnail is None:
                pending_images.setdefault(
                    image_url,
                    [],
                ).append(item_id)

        self._load_tree_images(
            self.report_tree,
            pending_images,
            current_image_generation,
            "report_image_generation",
        )

    def export_current_report(self):
        if self.report_df is None:
            messagebox.showwarning(
                "No report",
                "Finish the comparison before exporting a report.",
                parent=self.root,
            )
            return

        safe_set_num = str(self.set_num).strip().replace("/", "-")
        export_timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        default_filename = f"{safe_set_num}_{export_timestamp}.xml"

        export_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export BrickLink wanted list or CSV report",
            initialdir=REPORTS_DIR,
            initialfile=default_filename,
            defaultextension=".xml",
            filetypes=[
                ("BrickLink Wanted List XML", "*.xml"),
                ("CSV verification report", "*.csv"),
            ],
        )
        if not export_path:
            return

        suffix = Path(export_path).suffix.lower()
        if suffix not in {".csv", ".xml"}:
            messagebox.showerror(
                "Unsupported format",
                "Choose either a .csv report or a .xml wanted list.",
                parent=self.root,
            )
            return

        # XML carries only missing quantities. CSV remains the attention-only
        # report shown in the Verification Report tab.
        wanted_rows = untranslated_rows = None
        if suffix == ".xml":
            if self.wanted_list_df is None:
                messagebox.showwarning(
                    "No wanted list",
                    "Finish the comparison again before exporting XML.",
                    parent=self.root,
                )
                return

            wanted_rows, untranslated_rows = select_wanted_list_rows(
                self.wanted_list_df
            )
            ambiguous_count = 0
            if "bricklink_part_candidates" in untranslated_rows.columns:
                ambiguous_count = int(
                    untranslated_rows["bricklink_part_candidates"].apply(
                        lambda value: len(parse_bricklink_candidates(value)) > 1
                    ).sum()
                )

            if wanted_rows.empty:
                warning = (
                    "No missing parts have a BrickLink part number and "
                    "color, so the wanted list would be empty."
                )
                if ambiguous_count:
                    warning += (
                        f"\n\n{ambiguous_count} missing part row(s) have "
                        "multiple BrickLink IDs and require review."
                    )
                messagebox.showwarning(
                    "Nothing to put on a wanted list",
                    warning,
                    parent=self.root,
                )
                return

        try:
            export_df = (
                self.wanted_list_df
                if suffix == ".xml"
                else self.report_df
            )
            saved_path = export_report(
                export_df,
                export_path,
                fmt=suffix.lstrip("."),
                overwrite=True,
            )
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror(
                "Could not export report",
                str(exc),
                parent=self.root,
            )
            return

        if suffix == ".xml":
            message = (
                f"Saved a BrickLink wanted list with "
                f"{len(wanted_rows)} item(s) to:\n{saved_path}\n\n"
                "Upload it on BrickLink using "
                "'Upload BrickLink XML format'."
            )

            if not untranslated_rows.empty:
                if ambiguous_count:
                    message += (
                        f"\n\n{ambiguous_count} missing part row(s) were left "
                        "out because multiple BrickLink IDs require review."
                    )
                other_untranslated_count = (
                    len(untranslated_rows) - ambiguous_count
                )
                if other_untranslated_count:
                    message += (
                        f"\n\n{other_untranslated_count} missing part row(s) "
                        "were left out because no BrickLink part number or "
                        "color is known for them."
                    )

            self.status_var.set(
                f"Exported {len(wanted_rows)} wanted-list item(s) to "
                f"{saved_path}."
            )
        else:
            message = f"Saved to:\n{saved_path}"
            self.status_var.set(f"Exported report to {saved_path}.")

        messagebox.showinfo(
            "Report exported",
            message,
            parent=self.root,
        )

    # =========================================================================
    # Shared UI helpers
    # =========================================================================
    def _show_busy_action_message(self, action):
        """Explain why an editing action is unavailable during background work."""
        operation = str(getattr(self, "busy_status", "") or "Working...")
        self.status_var.set(f"{operation} {action} is temporarily disabled.")
        return "break"

    def _run_background(self, status, work, on_success):
        """Run API work without freezing the Tkinter event loop."""
        if self.busy:
            messagebox.showinfo(
                "Please wait",
                "Another operation is still running.",
                parent=self.root,
            )
            return

        self.busy = True
        self.busy_status = status
        self.root.configure(cursor="watch")
        self.status_var.set(status)
        self._update_command_states()
        self._update_review_variations_button()
        self._refresh_data_update_dialog()

        def run():
            try:
                result = work()
            except (
                SetNotFoundError,
                RebrickableAPIError,
                ValueError,
                OSError,
            ) as exc:
                self.root.after(
                    0,
                    lambda error=exc: self._background_error(error),
                )
            except Exception as exc:
                self.root.after(
                    0,
                    lambda error=exc: self._background_error(error),
                )
            else:
                self.root.after(
                    0,
                    lambda value=result: self._background_success(
                        value,
                        on_success,
                    ),
                )

        threading.Thread(target=run, daemon=True).start()

    def _background_success(self, result, on_success):
        self.busy = False
        self.busy_status = ""
        self.root.configure(cursor="")
        on_success(result)
        self._update_command_states()
        self._update_review_variations_button()
        self._refresh_data_update_dialog()

    def _background_error(self, error):
        self.busy = False
        self.busy_status = ""
        self.root.configure(cursor="")
        self.status_var.set("Operation failed.")
        self._update_command_states()
        self._update_review_variations_button()
        self._refresh_data_update_dialog()
        messagebox.showerror(
            "Operation failed",
            str(error),
            parent=self.root,
        )

    @staticmethod
    def _clear_tree(tree):
        children = tree.get_children()
        if children:
            tree.delete(*children)

    @staticmethod
    def _display_number(value):
        if pd.isna(value):
            return ""
        return int(value)

    @staticmethod
    def _display_text(value):
        if value is None or pd.isna(value):
            return ""
        return str(value)

    @staticmethod
    def _display_bricklink_identifier(value, numeric=False):
        """Make a missing export identifier visible without changing its value."""
        if value is None or pd.isna(value) or not str(value).strip():
            return "Not mapped"
        if numeric:
            return int(value)
        return str(value).strip()


def main():
    root = tk.Tk()
    LegoVerificationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
