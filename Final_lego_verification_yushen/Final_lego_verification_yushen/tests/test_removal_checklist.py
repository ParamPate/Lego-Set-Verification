import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import pandas as pd

from src.storage import load_job, save_job
from tkinter_app import LegoVerificationApp


class RemovalChecklistTests(unittest.TestCase):
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

    @staticmethod
    def report_rows():
        return pd.DataFrame([
            {
                "part_num": "3001",
                "bricklink_part_num": "3001",
                "color_id": 1,
                "part_name": "Brick 2 x 4",
                "color_name": "Blue",
                "status": "Extra",
                "quantity_extra": 2,
                "image_url": "https://example.test/3001-blue.png",
            },
            {
                "part_num": "98138",
                "bricklink_part_num": "98138",
                "color_id": 5,
                "part_name": "Tile Round 1 x 1",
                "color_name": "Red",
                "status": "Not in set",
                "quantity_extra": 3,
                "image_url": "https://example.test/98138-red.png",
            },
            {
                "part_num": "87552",
                "bricklink_part_num": "87552",
                "color_id": 11,
                "part_name": "Panel",
                "color_name": "Black",
                "status": "Missing",
                "quantity_extra": 0,
                "quantity_missing": 1,
            },
        ])

    def test_removal_state_round_trips_and_legacy_jobs_load_empty(self):
        user_input_df = pd.DataFrame([{
            "part_num": "3001",
            "color_id": 1,
            "quantity_have": 4,
        }])
        removal_states_df = pd.DataFrame([{
            "part_num": "3001",
            "color_id": 1,
            "quantity_to_remove": 2,
            "removed": True,
        }])

        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            save_job(
                jobs_dir,
                "current",
                "75375",
                user_input_df,
                removal_states_df=removal_states_df,
            )
            loaded = load_job(jobs_dir, "current")

            with open(
                jobs_dir / "legacy.json",
                "w",
                encoding="utf-8",
            ) as legacy_file:
                json.dump(
                    {
                        "job_id": "legacy",
                        "set_num": "75375",
                        "status": "complete",
                        "user_input": [],
                    },
                    legacy_file,
                )
            legacy_loaded = load_job(jobs_dir, "legacy")

        saved_state = loaded["removal_states_df"].iloc[0]
        self.assertEqual(saved_state["quantity_to_remove"], 2)
        self.assertTrue(saved_state["removed"])
        self.assertTrue(legacy_loaded["removal_states_df"].empty)

    def test_saved_removed_state_requires_same_part_color_and_quantity(self):
        saved_states = pd.DataFrame([
            {
                "part_num": "3001",
                "color_id": 1,
                "quantity_to_remove": 2,
                "removed": True,
            },
            {
                "part_num": "98138",
                "color_id": 5,
                "quantity_to_remove": 2,
                "removed": True,
            },
        ])

        result = LegoVerificationApp._attach_removal_states(
            self.report_rows(),
            saved_states,
        )

        self.assertEqual(
            result["removal_status"].tolist(),
            ["Removed", "Pending", ""],
        )

    def test_setting_removal_updates_report_compact_state_and_dirty_flag(self):
        app = LegoVerificationApp.__new__(LegoVerificationApp)
        app.workflow_stage = app.report_review_stage
        app.removal_states_df = pd.DataFrame(
            columns=app.removal_state_columns
        )
        app.report_df = app._attach_removal_states(
            self.report_rows(),
            app.removal_states_df,
        )
        dirty_calls = []
        app._mark_job_dirty = lambda: dirty_calls.append(True)

        self.assertTrue(app._set_removal_state("3001", 1, True))
        self.assertEqual(app.report_df.iloc[0]["removal_status"], "Removed")
        self.assertEqual(len(dirty_calls), 1)
        saved_state = app.removal_states_df[
            app.removal_states_df["part_num"].eq("3001")
        ].iloc[0]
        self.assertEqual(saved_state["quantity_to_remove"], 2)
        self.assertTrue(saved_state["removed"])

        self.assertTrue(app._set_removal_state("3001", 1, False))
        self.assertEqual(app.report_df.iloc[0]["removal_status"], "Pending")
        self.assertFalse(
            app.removal_states_df[
                app.removal_states_df["part_num"].eq("3001")
            ].iloc[0]["removed"]
        )

    def test_checklist_shows_only_surplus_rows_and_updates_report_column(self):
        app = self.make_app()
        app.workflow_stage = app.report_review_stage
        app.report_df = app._attach_removal_states(
            self.report_rows(),
            pd.DataFrame(),
        )
        app.wanted_list_df = app.report_df.copy()
        app._sync_removal_states_from_report()

        with patch.object(app, "_load_tree_images"):
            app.open_removal_checklist()

        children = app.removal_tree.get_children()
        self.assertEqual(len(children), 2)
        self.assertIn("2 pending", str(app.removal_checklist_button["text"]))
        first_item = app.removal_tree.selection()[0]
        first_key = app.removal_row_keys[first_item]

        with (
            patch.object(app, "_load_tree_images"),
            patch.object(app, "_render_report"),
        ):
            app._set_selected_removal_state(True)

        changed_row = app.report_df[
            LegoVerificationApp._part_color_mask(
                app.report_df,
                first_key[0],
                first_key[1],
            )
        ].iloc[0]
        self.assertEqual(changed_row["removal_status"], "Removed")
        self.assertEqual(app._pending_removal_count(), 1)

    def test_finish_is_blocked_until_every_surplus_row_is_removed(self):
        app = self.make_app()
        app.set_num = "75375"
        app.job_id = "job-75375"
        app.parts_df = pd.DataFrame([{"part_num": "3001"}])
        app.user_input_df = pd.DataFrame([{
            "part_num": "3001",
            "color_id": 1,
            "quantity_have": 4,
        }])
        app.workflow_stage = app.report_review_stage
        app.report_df = app._attach_removal_states(
            self.report_rows().iloc[[0]],
            pd.DataFrame(),
        )
        app.wanted_list_df = app.report_df.copy()
        app._sync_removal_states_from_report()

        with (
            patch("tkinter_app.messagebox.showwarning") as warning,
            patch("tkinter_app.messagebox.askyesno") as confirm,
            patch.object(app, "open_removal_checklist") as open_checklist,
        ):
            app.finish_verification()

        warning.assert_called_once()
        confirm.assert_not_called()
        open_checklist.assert_called_once()
        self.assertEqual(app.workflow_stage, app.report_review_stage)

        app._set_removal_state("3001", 1, True)
        with (
            patch("tkinter_app.messagebox.askyesno", return_value=True),
            patch.object(app, "_save_active_job"),
            patch.object(app, "_refresh_saved_jobs"),
            patch.object(app, "_update_review_variations_button"),
        ):
            app.finish_verification()

        self.assertEqual(app.workflow_stage, app.complete_stage)

    def test_compare_automatically_opens_checklist_for_surplus_rows(self):
        app = self.make_app()
        app.set_num = "75375"
        app.job_id = "job-75375"
        comparison_df = self.report_rows().copy()
        report_df = comparison_df.iloc[[0]].copy()

        with (
            patch.object(app, "_render_report"),
            patch.object(app, "_save_active_job"),
            patch.object(app, "_refresh_saved_jobs"),
            patch.object(app, "open_removal_checklist") as open_checklist,
        ):
            app._finish_comparison(
                (comparison_df, report_df, pd.DataFrame())
            )

        open_checklist.assert_called_once()
        self.assertEqual(app.workflow_stage, app.report_review_stage)
        self.assertEqual(app.report_df.iloc[0]["removal_status"], "Pending")


if __name__ == "__main__":
    unittest.main()
