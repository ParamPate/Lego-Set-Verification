import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import pandas as pd

from src.storage import load_job, save_job
from tkinter_app import LegoVerificationApp


class ReportRemarkPersistenceTests(unittest.TestCase):
    def make_app(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")

        root.withdraw()
        with (
            patch("tkinter_app.list_jobs", return_value=[]),
            patch.object(LegoVerificationApp, "_poll_image_queue"),
            patch.object(root, "after"),
        ):
            app = LegoVerificationApp(root)
        self.addCleanup(root.destroy)
        return app

    def test_remarks_round_trip_and_legacy_jobs_load_empty_state(self):
        user_input_df = pd.DataFrame([{
            "part_num": "87552",
            "color_id": 11,
            "quantity_have": 1,
        }])
        remarks_df = pd.DataFrame([{
            "part_num": "87552",
            "color_id": 11,
            "wanted_list_remark": "Bag 3",
        }])

        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            save_job(
                jobs_dir,
                "current",
                "75375",
                user_input_df,
                wanted_list_remarks_df=remarks_df,
            )
            loaded = load_job(jobs_dir, "current")

            legacy_data = {
                "job_id": "legacy",
                "set_num": "75375",
                "status": "complete",
                "user_input": [],
            }
            with open(
                jobs_dir / "legacy.json",
                "w",
                encoding="utf-8",
            ) as legacy_file:
                json.dump(legacy_data, legacy_file)
            legacy_loaded = load_job(jobs_dir, "legacy")

        self.assertEqual(
            loaded["wanted_list_remarks_df"].iloc[0][
                "wanted_list_remark"
            ],
            "Bag 3",
        )
        self.assertTrue(legacy_loaded["wanted_list_remarks_df"].empty)

    def test_saved_remarks_reattach_by_part_and_color(self):
        comparison_df = pd.DataFrame([
            {"part_num": "3001", "color_id": 1, "status": "Missing"},
            {"part_num": "3001", "color_id": 5, "status": "Missing"},
            {"part_num": "3001", "color_id": 7, "status": "Extra"},
        ])
        remarks_df = pd.DataFrame([
            {
                "part_num": "3001",
                "color_id": 1,
                "wanted_list_remark": "Bag 1",
            },
            {
                "part_num": "3001",
                "color_id": 5,
                "wanted_list_remark": "Bag 2",
            },
            {
                "part_num": "3001",
                "color_id": 7,
                "wanted_list_remark": "Old missing-part note",
            },
        ])

        result_df = LegoVerificationApp._attach_wanted_list_remarks(
            comparison_df,
            remarks_df,
        )

        self.assertEqual(
            result_df["wanted_list_remark"].tolist(),
            ["Bag 1", "Bag 2", ""],
        )

    def test_setting_remark_updates_both_unfiltered_sources_and_dirty_state(self):
        app = LegoVerificationApp.__new__(LegoVerificationApp)
        app.wanted_list_remarks_df = pd.DataFrame(
            columns=app.wanted_list_remark_columns
        )
        app.report_df = pd.DataFrame([{
            "part_num": "87552",
            "color_id": 11,
            "status": "Missing",
        }])
        app.wanted_list_df = pd.DataFrame([
            {
                "part_num": "87552",
                "color_id": 11,
                "status": "Missing",
            },
            {
                "part_num": "3001",
                "color_id": 1,
                "status": "Complete",
            },
        ])
        dirty_calls = []
        app._mark_job_dirty = lambda: dirty_calls.append(True)

        self.assertTrue(
            app._set_wanted_list_remark("87552", 11, "Bag 3")
        )
        self.assertEqual(len(dirty_calls), 1)
        self.assertEqual(
            app.report_df.iloc[0]["wanted_list_remark"],
            "Bag 3",
        )
        self.assertEqual(
            app.wanted_list_df.iloc[0]["wanted_list_remark"],
            "Bag 3",
        )
        self.assertEqual(
            app.wanted_list_df.iloc[1]["wanted_list_remark"],
            "",
        )

        self.assertTrue(app._set_wanted_list_remark("87552", 11, ""))
        self.assertTrue(app.wanted_list_remarks_df.empty)
        self.assertEqual(
            app.wanted_list_df.iloc[0]["wanted_list_remark"],
            "",
        )

    def test_report_editor_is_available_only_for_missing_rows(self):
        app = self.make_app()
        app.report_df = pd.DataFrame([
            {
                "part_num": "87552",
                "color_id": 11,
                "status": "Missing",
                "wanted_list_remark": "",
            },
            {
                "part_num": "3001",
                "color_id": 1,
                "status": "Extra",
                "wanted_list_remark": "",
            },
        ])
        app.wanted_list_df = app.report_df.copy()

        with patch.object(app, "_load_tree_images"):
            app._render_report()

        self.assertEqual(
            app.report_heading_labels["xml_remark"],
            "Bag / XML Remark",
        )
        missing_item = next(
            item_id
            for item_id, row_key in app.report_row_keys.items()
            if row_key[2] == "Missing"
        )
        extra_item = next(
            item_id
            for item_id, row_key in app.report_row_keys.items()
            if row_key[2] == "Extra"
        )
        display_columns = list(app.report_tree["displaycolumns"])
        remark_column_id = f"#{display_columns.index('xml_remark') + 1}"
        event = SimpleNamespace(x=1, y=1)

        with (
            patch.object(
                app.report_tree,
                "identify_row",
                return_value=missing_item,
            ),
            patch.object(
                app.report_tree,
                "identify_column",
                return_value=remark_column_id,
            ),
            patch.object(
                app.report_tree,
                "bbox",
                return_value=(0, 0, 220, 24),
            ),
        ):
            app._begin_report_remark_edit(event)

        editors = [
            child
            for child in app.report_tree.winfo_children()
            if isinstance(child, ttk.Entry)
        ]
        self.assertEqual(len(editors), 1)
        editors[0].destroy()

        with (
            patch.object(
                app.report_tree,
                "identify_row",
                return_value=extra_item,
            ),
            patch.object(
                app.report_tree,
                "identify_column",
                return_value=remark_column_id,
            ),
        ):
            app._begin_report_remark_edit(event)

        self.assertIn("only for missing parts", app.status_var.get())

    def test_bulk_assignment_updates_selected_missing_rows_and_can_clear_them(self):
        app = self.make_app()
        app.workflow_stage = app.report_review_stage
        app.report_df = pd.DataFrame([
            {
                "part_num": "87552",
                "color_id": 11,
                "status": "Missing",
                "wanted_list_remark": "",
            },
            {
                "part_num": "3001",
                "color_id": 1,
                "status": "Missing",
                "wanted_list_remark": "",
            },
            {
                "part_num": "98138",
                "color_id": 5,
                "status": "Extra",
                "wanted_list_remark": "",
            },
        ])
        app.wanted_list_df = app.report_df.copy()

        with patch.object(app, "_load_tree_images"):
            app._render_report()

        selected_items = tuple(app.report_row_keys)
        app.report_tree.selection_set(*selected_items)
        app._update_bulk_remark_button()
        self.assertEqual(str(app.report_tree["selectmode"]), "extended")
        self.assertEqual(str(app.bulk_remark_button["state"]), "normal")

        with (
            patch(
                "tkinter_app.simpledialog.askstring",
                return_value="Bag 4",
            ),
            patch.object(app, "_load_tree_images"),
        ):
            app.assign_bulk_report_remark()

        missing_mask = app.report_df["status"].eq("Missing")
        self.assertEqual(
            app.report_df.loc[
                missing_mask,
                "wanted_list_remark",
            ].tolist(),
            ["Bag 4", "Bag 4"],
        )
        self.assertEqual(
            app.wanted_list_df.loc[
                missing_mask,
                "wanted_list_remark",
            ].tolist(),
            ["Bag 4", "Bag 4"],
        )
        self.assertEqual(
            app.report_df.loc[
                app.report_df["status"].eq("Extra"),
                "wanted_list_remark",
            ].iloc[0],
            "",
        )
        self.assertEqual(len(app.wanted_list_remarks_df), 2)
        self.assertTrue(app.job_dirty)

        missing_items = tuple(
            item_id
            for item_id, row_key in app.report_row_keys.items()
            if row_key[2] == "Missing"
        )
        app.report_tree.selection_set(*missing_items)
        with (
            patch(
                "tkinter_app.simpledialog.askstring",
                return_value="",
            ),
            patch.object(app, "_load_tree_images"),
        ):
            app.assign_bulk_report_remark()

        self.assertTrue(app.wanted_list_remarks_df.empty)
        self.assertTrue(
            app.report_df.loc[
                app.report_df["status"].eq("Missing"),
                "wanted_list_remark",
            ].eq("").all()
        )

    def test_bulk_assignment_button_requires_editable_missing_selection(self):
        app = self.make_app()
        app.workflow_stage = app.report_review_stage
        app.report_df = pd.DataFrame([
            {
                "part_num": "87552",
                "color_id": 11,
                "status": "Missing",
                "wanted_list_remark": "",
            },
            {
                "part_num": "98138",
                "color_id": 5,
                "status": "Extra",
                "wanted_list_remark": "",
            },
        ])
        app.wanted_list_df = app.report_df.copy()

        with patch.object(app, "_load_tree_images"):
            app._render_report()

        missing_item = next(
            item_id
            for item_id, row_key in app.report_row_keys.items()
            if row_key[2] == "Missing"
        )
        extra_item = next(
            item_id
            for item_id, row_key in app.report_row_keys.items()
            if row_key[2] == "Extra"
        )

        app.report_tree.selection_set(extra_item)
        app._update_bulk_remark_button()
        self.assertEqual(str(app.bulk_remark_button["state"]), "disabled")

        app.report_tree.selection_set(missing_item)
        app._update_bulk_remark_button()
        self.assertEqual(str(app.bulk_remark_button["state"]), "normal")

        app.workflow_stage = app.complete_stage
        app._update_bulk_remark_button()
        self.assertEqual(str(app.bulk_remark_button["state"]), "disabled")


if __name__ == "__main__":
    unittest.main()
