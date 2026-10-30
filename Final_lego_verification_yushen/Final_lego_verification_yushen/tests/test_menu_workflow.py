import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import pandas as pd

from config import JOBS_DIR
from src.storage import delete_job
from tkinter_app import LegoVerificationApp


def saved_job(position, set_num=None, status="in_progress"):
    return {
        "job_id": f"job-{position}",
        "set_num": str(set_num if set_num is not None else 1000 + position),
        "status": status,
        "updated_at": f"2026-08-{position + 1:02d}T12:30:00",
    }


class MenuWorkflowTests(unittest.TestCase):
    def make_app(self, jobs=None):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")

        root.withdraw()
        with (
            patch("tkinter_app.list_jobs", return_value=jobs or []),
            patch.object(LegoVerificationApp, "_poll_image_queue"),
            patch.object(root, "after"),
        ):
            app = LegoVerificationApp(root)
        def cleanup_root():
            for callback_id in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(callback_id)
            root.destroy()

        self.addCleanup(cleanup_root)
        return root, app

    def select_saved_job(self, app, job_id):
        item_id = next(
            item_id
            for item_id, mapped_job_id in app.saved_job_row_lookup.items()
            if mapped_job_id == job_id
        )
        app.saved_job_tree.selection_set(item_id)
        app.saved_job_tree.focus(item_id)
        app._update_command_states()

    def test_main_table_tabs_are_large_and_mark_the_active_table(self):
        root, app = self.make_app()
        style = ttk.Style(root)

        self.assertEqual(
            str(app.main_table_notebook["style"]),
            "Main.TNotebook",
        )
        self.assertIn(
            "12",
            str(style.lookup("Main.TNotebook.Tab", "font")),
        )
        self.assertEqual(
            style.lookup("Main.TNotebook.Tab", "foreground"),
            "#6b6b6b",
        )
        self.assertEqual(
            style.lookup(
                "Main.TNotebook.Tab",
                "foreground",
                ("selected",),
            ),
            "#000000",
        )
        self.assertTrue(
            app.main_table_notebook.tab(app.parts_tab, "text").startswith(
                "▶  Required Parts"
            )
        )
        self.assertEqual(
            app.main_table_notebook.tab(app.report_tab, "text"),
            "Verification Report",
        )

        app.main_table_notebook.select(app.report_tab)
        root.update()

        self.assertEqual(
            app.main_table_notebook.tab(app.parts_tab, "text"),
            "Required Parts",
        )
        self.assertTrue(
            app.main_table_notebook.tab(app.report_tab, "text").startswith(
                "▶  Verification Report"
            )
        )

    def test_file_menu_replaces_persistent_controls_and_tracks_state(self):
        _root, app = self.make_app()

        top_labels = [
            app.menu_bar.entrycget(index, "label")
            for index in range(app.menu_bar.index("end") + 1)
        ]
        self.assertEqual(top_labels, ["File", "View", "Settings"])
        self.assertFalse(hasattr(app, "saved_job_combo"))
        self.assertEqual(
            app.file_menu.entrycget(
                app.file_menu_indices["start"],
                "state",
            ),
            "normal",
        )
        for command_name in ("resume", "prefill", "save", "export"):
            self.assertEqual(
                app.file_menu.entrycget(
                    app.file_menu_indices[command_name],
                    "state",
                ),
                "disabled",
            )
        self.assertEqual(str(app.compare_parts_button["state"]), "disabled")

        app.parts_df = pd.DataFrame([{"part_num": "3001"}])
        app.report_df = pd.DataFrame([{"status": "Missing"}])
        app._update_command_states()
        for command_name in ("prefill", "save", "export"):
            self.assertEqual(
                app.file_menu.entrycget(
                    app.file_menu_indices[command_name],
                    "state",
                ),
                "normal",
            )
        self.assertEqual(str(app.compare_parts_button["state"]), "normal")

        app.busy = True
        app._update_command_states()
        self.assertEqual(
            app.file_menu.entrycget(
                app.file_menu_indices["start"],
                "state",
            ),
            "disabled",
        )
        self.assertEqual(str(app.compare_parts_button["state"]), "disabled")

    def test_variation_alternative_details_hide_and_preserve_results(self):
        root, app = self.make_app()
        root.deiconify()
        root.update()
        main_path = str(app.main_tables_frame)
        details_path = str(app.relationship_details_frame)

        self.assertEqual(
            app.view_menu.entrycget(0, "label"),
            "Variation/Alternative Details",
        )
        self.assertFalse(app.relationship_details_visible_var.get())
        self.assertEqual(
            tuple(str(pane) for pane in app.content_paned.panes()),
            (main_path,),
        )

        app.relationship_tree.insert(
            "",
            "end",
            iid="preserved_relationship",
            values=("Mold variation", "3001", "Brick", "Yes", "Red", "4"),
        )
        app.relationship_details_visible_var.set(True)
        app._toggle_relationship_details()
        root.after(75, root.quit)
        root.mainloop()
        self.assertEqual(
            tuple(str(pane) for pane in app.content_paned.panes()),
            (main_path, details_path),
        )
        self.assertGreater(
            app.relationship_details_frame.winfo_height(),
            150,
        )

        app.relationship_details_visible_var.set(False)
        app._toggle_relationship_details()
        self.assertEqual(
            tuple(str(pane) for pane in app.content_paned.panes()),
            (main_path,),
        )
        self.assertEqual(
            app.relationship_tree.get_children(),
            ("preserved_relationship",),
        )

        app.relationship_details_visible_var.set(True)
        app._toggle_relationship_details()
        app._toggle_relationship_details()
        self.assertEqual(
            tuple(str(pane) for pane in app.content_paned.panes()),
            (main_path, details_path),
        )

    def test_resume_menu_shows_recent_jobs_and_searches_all_jobs(self):
        jobs = [saved_job(position) for position in range(14)]
        _root, app = self.make_app(jobs)

        self.assertEqual(len(app.saved_job_lookup), 12)
        self.assertEqual(
            app.resume_jobs_menu.entrycget(
                app.resume_jobs_menu.index("end"),
                "label",
            ),
            "View All Saved Jobs...",
        )

        app.open_saved_jobs_dialog()
        app.saved_jobs_search_var.set("1013")
        app._render_saved_jobs_dialog()
        matching_items = app.saved_job_tree.get_children()
        self.assertEqual(len(matching_items), 1)
        self.assertEqual(
            app.saved_job_tree.item(matching_items[0], "values")[0],
            "1013",
        )
        self.assertIn("1 of 14", app.saved_jobs_status_var.get())

    def test_saved_job_sort_tolerates_non_numeric_and_invalid_metadata(self):
        _root, app = self.make_app()
        jobs = [
            {
                "job_id": "unusual",
                "set_num": "custom-set",
                "status": "unknown",
                "updated_at": "not-a-date",
            },
            saved_job(0, set_num=6033),
        ]

        ordered = sorted(jobs, key=app._saved_job_sort_key)

        self.assertEqual(ordered[0]["set_num"], "6033")
        self.assertEqual(ordered[1]["set_num"], "custom-set")

    def test_saved_job_deletion_confirms_details_and_refreshes_both_views(self):
        jobs = [saved_job(0), saved_job(1)]
        _root, app = self.make_app(jobs)
        app.open_saved_jobs_dialog()
        self.select_saved_job(app, "job-0")

        with (
            patch(
                "tkinter_app.messagebox.askyesno",
                return_value=True,
            ) as confirm,
            patch("tkinter_app.delete_job") as delete,
            patch("tkinter_app.list_jobs", return_value=[jobs[1]]),
        ):
            app._delete_job_from_saved_jobs_dialog()

        delete.assert_called_once_with(JOBS_DIR, "job-0")
        confirmation_text = confirm.call_args.args[1]
        self.assertIn("Set: 1000", confirmation_text)
        self.assertIn("Status: In Progress", confirmation_text)
        self.assertIn("Last updated: 2026-08-01 12:30", confirmation_text)
        self.assertIn("cannot be undone", confirmation_text)
        self.assertEqual(
            [job["job_id"] for job in app.saved_jobs],
            ["job-1"],
        )
        self.assertEqual(list(app.saved_job_lookup.values()), ["job-1"])
        self.assertIn("1 of 1", app.saved_jobs_status_var.get())
        self.assertEqual(
            app.status_var.get(),
            "Deleted saved job for set 1000.",
        )

    def test_saved_job_deletion_can_be_cancelled(self):
        jobs = [saved_job(0)]
        _root, app = self.make_app(jobs)
        app.open_saved_jobs_dialog()

        with (
            patch("tkinter_app.messagebox.askyesno", return_value=False),
            patch("tkinter_app.delete_job") as delete,
        ):
            app._delete_job_from_saved_jobs_dialog()

        delete.assert_not_called()
        self.assertEqual(
            [job["job_id"] for job in app.saved_jobs],
            ["job-0"],
        )

    def test_currently_open_saved_job_cannot_be_deleted(self):
        jobs = [saved_job(0), saved_job(1)]
        _root, app = self.make_app(jobs)
        app.open_saved_jobs_dialog()
        self.select_saved_job(app, "job-0")
        app.job_id = "job-0"
        app._update_command_states()

        self.assertEqual(
            str(app.saved_jobs_delete_button["state"]),
            "disabled",
        )

        with (
            patch("tkinter_app.messagebox.showinfo") as info,
            patch("tkinter_app.delete_job") as delete,
        ):
            app._delete_job_from_saved_jobs_dialog()

        info.assert_called_once()
        delete.assert_not_called()

    def test_delete_job_rejects_path_like_job_ids(self):
        with self.assertRaisesRegex(ValueError, "Invalid saved job ID"):
            delete_job(JOBS_DIR, "../outside-saved-jobs")

    def test_unsaved_work_prompt_supports_save_discard_and_cancel(self):
        _root, app = self.make_app()
        app.parts_df = pd.DataFrame([{"part_num": "3001"}])
        app.set_num = "6033"
        app.job_dirty = True

        with (
            patch("tkinter_app.messagebox.askyesnocancel", return_value=True),
            patch.object(app, "save_progress", return_value=True) as save,
        ):
            self.assertTrue(app._confirm_leave_current_job())
            save.assert_called_once_with(show_confirmation=False)

        with patch(
            "tkinter_app.messagebox.askyesnocancel",
            return_value=False,
        ):
            self.assertTrue(app._confirm_leave_current_job())

        with patch(
            "tkinter_app.messagebox.askyesnocancel",
            return_value=None,
        ):
            self.assertFalse(app._confirm_leave_current_job())


if __name__ == "__main__":
    unittest.main()
