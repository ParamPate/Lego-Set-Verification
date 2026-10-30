import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import pandas as pd

from src.storage import load_job, save_job
from tkinter_app import LegoVerificationApp


class ReportWorkflowTests(unittest.TestCase):
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
    def loaded_state(app):
        app.set_num = "75375"
        app.job_id = "job-75375"
        app.parts_df = pd.DataFrame([{"part_num": "87552"}])
        app.user_input_df = pd.DataFrame([{
            "part_num": "87552",
            "color_id": 11,
            "quantity_have": 1,
        }])

    def test_storage_round_trips_workflow_and_exact_report_snapshots(self):
        user_input_df = pd.DataFrame([{
            "part_num": "87552",
            "color_id": 11,
            "quantity_have": 1,
        }])
        report_df = pd.DataFrame([{
            "part_num": "87552",
            "color_id": 11,
            "status": "Missing",
            "wanted_list_remark": "Bag 3",
        }])
        wanted_list_df = report_df.assign(quantity_missing=1)

        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            save_job(
                jobs_dir,
                "review",
                "75375",
                user_input_df,
                workflow_stage="report_review",
                report_snapshot_df=report_df,
                wanted_list_snapshot_df=wanted_list_df,
            )
            loaded = load_job(jobs_dir, "review")

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

        self.assertEqual(loaded["workflow_stage"], "report_review")
        self.assertTrue(loaded["has_report_snapshot"])
        self.assertTrue(loaded["has_wanted_list_snapshot"])
        self.assertEqual(
            loaded["report_snapshot_df"].iloc[0]["wanted_list_remark"],
            "Bag 3",
        )
        self.assertFalse(legacy_loaded["has_report_snapshot"])
        self.assertFalse(legacy_loaded["has_wanted_list_snapshot"])

    def test_interrupted_job_write_preserves_existing_save(self):
        user_input_df = pd.DataFrame([{
            "part_num": "87552",
            "color_id": 11,
            "quantity_have": 1,
        }])

        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            job_path = jobs_dir / "current.json"
            original = '{"job_id": "current", "status": "in_progress"}'
            job_path.write_text(original, encoding="utf-8")

            def interrupt_dump(payload, destination, indent):
                destination.write("{\"partial\":")
                raise OSError("interrupted write")

            with (
                patch("src.storage.json.dump", side_effect=interrupt_dump),
                self.assertRaisesRegex(OSError, "interrupted write"),
            ):
                save_job(
                    jobs_dir,
                    "current",
                    "75375",
                    user_input_df,
                )

            self.assertEqual(job_path.read_text(encoding="utf-8"), original)
            self.assertEqual(list(jobs_dir.glob("*.tmp")), [])

    def test_save_status_is_derived_only_from_workflow_stage(self):
        app = LegoVerificationApp.__new__(LegoVerificationApp)
        self.loaded_state(app)
        app.substitutions = []
        app.declined_substitutions = []
        app.variation_proposals = None
        app.import_have_df = None
        app.extra_part_metadata_df = pd.DataFrame()
        app.wanted_list_remarks_df = pd.DataFrame()
        app.removal_states_df = pd.DataFrame()
        app.report_df = pd.DataFrame([{"status": "Missing"}])
        app.wanted_list_df = app.report_df.copy()

        with patch("tkinter_app.save_job") as saved:
            app.workflow_stage = app.report_review_stage
            app._save_active_job()
            review_call = saved.call_args

            app.workflow_stage = app.complete_stage
            app._save_active_job()
            complete_call = saved.call_args

        self.assertEqual(review_call.kwargs["status"], "in_progress")
        self.assertEqual(
            review_call.kwargs["workflow_stage"],
            "report_review",
        )
        self.assertEqual(complete_call.kwargs["status"], "complete")
        self.assertEqual(
            complete_call.kwargs["workflow_stage"],
            "complete",
        )

    def test_compare_creates_in_progress_report_review_snapshot(self):
        app = self.make_app()
        self.loaded_state(app)
        comparison_df = pd.DataFrame([{
            "part_num": "87552",
            "color_id": 11,
            "status": "Missing",
            "quantity_missing": 1,
        }])
        report_df = comparison_df.copy()
        saved_stages = []

        with (
            patch.object(app, "_render_report"),
            patch.object(app, "_refresh_saved_jobs"),
            patch.object(
                app,
                "_save_active_job",
                side_effect=lambda: saved_stages.append(app.workflow_stage),
            ),
        ):
            app._finish_comparison(
                (comparison_df, report_df, pd.DataFrame())
            )

        self.assertEqual(app.workflow_stage, app.report_review_stage)
        self.assertEqual(saved_stages, [app.report_review_stage])
        self.assertFalse(app.job_dirty)
        self.assertEqual(
            app.main_table_notebook.select(),
            str(app.report_tab),
        )

    def test_report_review_and_complete_snapshots_restore_without_compare(self):
        app = self.make_app()
        self.loaded_state(app)
        report_df = pd.DataFrame([{
            "part_num": "87552",
            "status": "Missing",
        }])
        wanted_list_df = report_df.assign(quantity_missing=1)

        with patch.object(app, "_render_report") as render:
            restored = app._restore_saved_workflow({
                "workflow_stage": "complete",
                "has_report_snapshot": True,
                "has_wanted_list_snapshot": True,
                "report_snapshot_df": report_df,
                "wanted_list_snapshot_df": wanted_list_df,
            })

        self.assertTrue(restored)
        render.assert_called_once()
        self.assertEqual(app.workflow_stage, app.complete_stage)
        self.assertEqual(app.report_df.iloc[0]["part_num"], "87552")
        self.assertEqual(
            app.main_table_notebook.select(),
            str(app.report_tab),
        )

        with patch.object(app, "_render_report"):
            restored_legacy = app._restore_saved_workflow({
                "status": "complete",
                "has_report_snapshot": False,
                "has_wanted_list_snapshot": False,
            })

        self.assertFalse(restored_legacy)
        self.assertEqual(app.workflow_stage, app.parts_entry_stage)
        self.assertIsNone(app.report_df)
        self.assertEqual(
            app.main_table_notebook.select(),
            str(app.parts_tab),
        )

    def test_manual_mapping_selected_on_resume_invalidates_saved_report(self):
        app = self.make_app()
        display_df = pd.DataFrame([{
            "part_num": "87552",
            "part_name": "Panel",
            "color_id": 11,
            "bricklink_part_num": "87552b",
            "bricklink_part_candidates": '["87552a", "87552b"]',
        }])
        job_data = {
            "set_num": "75375",
            "user_input_df": pd.DataFrame([{
                "part_num": "87552",
                "color_id": 11,
                "quantity_have": 0,
            }]),
            "workflow_stage": app.report_review_stage,
            "has_report_snapshot": True,
            "has_wanted_list_snapshot": True,
            "report_snapshot_df": pd.DataFrame([{
                "part_num": "87552",
                "bricklink_part_num": "",
            }]),
            "wanted_list_snapshot_df": pd.DataFrame([{
                "part_num": "87552",
                "bricklink_part_num": "",
            }]),
        }

        with (
            patch.object(
                app,
                "_review_ambiguous_bricklink_mappings",
                return_value=(display_df, 1),
            ),
            patch.object(app, "_render_parts_table"),
            patch.object(app, "_clear_relationship_details"),
            patch.object(app, "_invalidate_report") as invalidate_report,
            patch.object(app, "_restore_saved_workflow") as restore_report,
        ):
            app._finish_resume_job(
                "saved-job",
                job_data,
                (
                    {"name": "Test Set"},
                    display_df,
                    pd.DataFrame(),
                    pd.DataFrame(),
                ),
            )

        invalidate_report.assert_called_once_with()
        restore_report.assert_not_called()
        self.assertTrue(app.job_dirty)
        self.assertIn("Saved 1 manual BrickLink mapping", app.status_var.get())

    def test_invalidating_report_returns_to_parts_without_auto_compare(self):
        app = self.make_app()
        self.loaded_state(app)
        app.workflow_stage = app.report_review_stage
        app.report_df = pd.DataFrame([{"part_num": "87552"}])
        app.wanted_list_df = app.report_df.copy()
        app.main_table_notebook.select(app.report_tab)

        with (
            patch.object(app, "_render_report"),
            patch.object(app, "compare_parts") as compare,
        ):
            app._invalidate_report()

        self.assertIsNone(app.report_df)
        self.assertIsNone(app.wanted_list_df)
        self.assertEqual(app.workflow_stage, app.parts_entry_stage)
        self.assertEqual(
            app.main_table_notebook.select(),
            str(app.parts_tab),
        )
        compare.assert_not_called()

    def test_finish_and_reopen_are_the_only_complete_status_transitions(self):
        app = self.make_app()
        self.loaded_state(app)
        app.workflow_stage = app.report_review_stage
        app.report_df = pd.DataFrame([{"status": "Missing"}])
        app.wanted_list_df = app.report_df.copy()
        saved_stages = []

        with (
            patch("tkinter_app.messagebox.askyesno", return_value=True),
            patch.object(app, "_refresh_saved_jobs"),
            patch.object(app, "_update_review_variations_button"),
            patch.object(
                app,
                "_save_active_job",
                side_effect=lambda: saved_stages.append(app.workflow_stage),
            ),
        ):
            app.finish_verification()
            self.assertEqual(app.workflow_stage, app.complete_stage)
            self.assertEqual(
                str(app.finish_verification_button["text"]),
                "Reopen Verification",
            )

            app.reopen_verification()

        self.assertEqual(
            saved_stages,
            [app.complete_stage, app.report_review_stage],
        )
        self.assertEqual(app.workflow_stage, app.report_review_stage)
        self.assertEqual(
            str(app.finish_verification_button["text"]),
            "Finish Verification",
        )


if __name__ == "__main__":
    unittest.main()
