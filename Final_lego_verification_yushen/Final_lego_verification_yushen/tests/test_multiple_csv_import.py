import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.storage import load_job, save_job
from tkinter_app import LegoVerificationApp


class StatusValue:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


def have_frame(rows):
    return pd.DataFrame(rows, columns=[
        "part_num",
        "color_id",
        "quantity_have",
    ])


def summary(total, rows):
    return SimpleNamespace(
        total_quantity=total,
        distinct_combinations=rows,
        has_warnings=False,
        as_lines=lambda: [f"Imported quantity: {total}"],
    )


def proposal_frame():
    return pd.DataFrame([{
        "csv_part_num": "source",
        "csv_color_id": 5,
        "quantity_have": 2,
        "set_part_num": "target",
        "set_part_name": "Target",
        "set_color_id": 5,
        "set_color_name": "Red",
        "quantity_required": 3,
        "rel_type": "M",
    }])


def build_app(rows):
    app = LegoVerificationApp.__new__(LegoVerificationApp)
    app.root = None
    app.user_input_df = have_frame(rows)
    app.parts_df = have_frame([])
    app.substitutions = []
    app.declined_substitutions = []
    app.variation_proposals = None
    app.import_have_df = None
    app.import_history = []
    app.status_var = StatusValue()
    app.summary_calls = []
    app.dirty_calls = 0
    app._show_import_summary = app.summary_calls.append
    app._update_review_variations_button = lambda: None
    app._render_parts_table = lambda: None
    app._invalidate_report = lambda: None

    def mark_dirty():
        app.dirty_calls += 1

    app._mark_job_dirty = mark_dirty
    return app


def quantities(df):
    return {
        (str(row["part_num"]), int(row["color_id"])):
            int(row["quantity_have"])
        for _, row in df.iterrows()
    }


class MultipleCsvImportTests(unittest.TestCase):
    def test_post_variation_import_keeps_unrelated_new_direct_row(self):
        app = build_app([
            {"part_num": "target", "color_id": 5, "quantity_have": 3},
        ])
        proposals_df = proposal_frame()
        app.variation_proposals = proposals_df.copy()
        app.import_have_df = have_frame([
            {"part_num": "target", "color_id": 5, "quantity_have": 1},
            {"part_num": "source", "color_id": 5, "quantity_have": 2},
        ])
        allocated_df = proposals_df.assign(quantity_allocated=2)
        app._record_substitution_choices(proposals_df, allocated_df)
        app._choose_csv_import_mode = lambda **_kwargs: app.import_mode_fill
        app._review_variation_substitutions = (
            lambda merged, **_kwargs: merged.assign(quantity_allocated=2)
        )
        new_direct_row = have_frame([
            {"part_num": "new-direct", "color_id": 7, "quantity_have": 4},
        ])

        app._finish_csv_import(
            "second.csv",
            (new_direct_row, summary(4, 1), pd.DataFrame(), "hash-2"),
        )

        self.assertEqual(
            quantities(app.user_input_df),
            {("target", 5): 3, ("new-direct", 7): 4},
        )

    def test_add_mode_accumulates_existing_and_new_rows(self):
        app = build_app([
            {"part_num": "existing", "color_id": 1, "quantity_have": 5},
            {"part_num": "blank", "color_id": 2, "quantity_have": 0},
        ])
        app._choose_csv_import_mode = lambda **_kwargs: app.import_mode_add
        incoming_df = have_frame([
            {"part_num": "existing", "color_id": 1, "quantity_have": 2},
            {"part_num": "blank", "color_id": 2, "quantity_have": 3},
            {"part_num": "new", "color_id": 3, "quantity_have": 4},
        ])

        app._finish_csv_import(
            "batch-2.csv",
            (incoming_df, summary(9, 3), pd.DataFrame(), "hash-add"),
        )

        self.assertEqual(
            quantities(app.user_input_df),
            {("existing", 1): 7, ("blank", 2): 3, ("new", 3): 4},
        )
        self.assertEqual(app.import_history[-1]["mode"], app.import_mode_add)
        contribution_total = sum(
            row["quantity"]
            for row in app.import_history[-1]["contributions"]
        )
        self.assertEqual(contribution_total, 9)

    def test_fill_mode_preserves_nonzero_and_fills_zero(self):
        app = build_app([
            {"part_num": "existing", "color_id": 1, "quantity_have": 5},
            {"part_num": "blank", "color_id": 2, "quantity_have": 0},
        ])
        app._choose_csv_import_mode = lambda **_kwargs: app.import_mode_fill
        incoming_df = have_frame([
            {"part_num": "existing", "color_id": 1, "quantity_have": 2},
            {"part_num": "blank", "color_id": 2, "quantity_have": 3},
        ])

        app._finish_csv_import(
            "fill.csv",
            (incoming_df, summary(5, 2), pd.DataFrame(), "hash-fill"),
        )

        self.assertEqual(
            quantities(app.user_input_df),
            {("existing", 1): 5, ("blank", 2): 3},
        )
        contributions = app.import_history[-1]["contributions"]
        self.assertEqual(len(contributions), 1)
        self.assertEqual(contributions[0]["part_num"], "blank")
        self.assertEqual(contributions[0]["quantity"], 3)

    def test_cancel_leaves_all_state_unchanged(self):
        app = build_app([
            {"part_num": "existing", "color_id": 1, "quantity_have": 5},
        ])
        original_df = app.user_input_df.copy(deep=True)
        app._choose_csv_import_mode = lambda **_kwargs: app.import_mode_cancel

        app._finish_csv_import(
            "cancel.csv",
            (
                have_frame([{
                    "part_num": "existing",
                    "color_id": 1,
                    "quantity_have": 2,
                }]),
                summary(2, 1),
                pd.DataFrame(),
                "hash-cancel",
            ),
        )

        pd.testing.assert_frame_equal(app.user_input_df, original_df)
        self.assertEqual(app.import_history, [])
        self.assertEqual(app.summary_calls, [])
        self.assertEqual(app.dirty_calls, 0)

    def test_duplicate_hash_is_reported_to_decision_dialog(self):
        app = build_app([
            {"part_num": "existing", "color_id": 1, "quantity_have": 5},
        ])
        app.import_history = [{"content_hash": "same-hash"}]
        captured = {}

        def choose(**kwargs):
            captured.update(kwargs)
            return app.import_mode_cancel

        app._choose_csv_import_mode = choose
        app._finish_csv_import(
            "duplicate.csv",
            (have_frame([]), summary(0, 0), pd.DataFrame(), "same-hash"),
        )

        self.assertTrue(captured["is_duplicate"])
        self.assertEqual(app.import_history, [{"content_hash": "same-hash"}])

    def test_variation_cancel_happens_before_summary_or_history_commit(self):
        app = build_app([
            {"part_num": "target", "color_id": 5, "quantity_have": 1},
        ])
        app._choose_csv_import_mode = lambda **_kwargs: app.import_mode_add
        app._review_variation_substitutions = lambda *_args, **_kwargs: None

        app._finish_csv_import(
            "variation.csv",
            (
                have_frame([{
                    "part_num": "source",
                    "color_id": 5,
                    "quantity_have": 2,
                }]),
                summary(2, 1),
                proposal_frame(),
                "hash-variation",
            ),
        )

        self.assertEqual(quantities(app.user_input_df), {("target", 5): 1})
        self.assertEqual(app.summary_calls, [])
        self.assertEqual(app.import_history, [])
        self.assertEqual(app.dirty_calls, 0)

    def test_import_history_round_trips_through_saved_job(self):
        history = [{
            "source_name": "batch.csv",
            "content_hash": "abc123",
            "imported_at": "2026-08-12 12:00:00",
            "mode": "add",
            "contributions": [{
                "part_num": "3001",
                "color_id": 5,
                "bricklink_color_id": None,
                "quantity": 2,
            }],
        }]

        with tempfile.TemporaryDirectory() as temp_dir:
            save_job(
                Path(temp_dir),
                "job",
                "1234",
                have_frame([]),
                import_history=history,
            )
            loaded = load_job(Path(temp_dir), "job")

        self.assertEqual(loaded["import_history"], history)


class MultipleCsvImportDialogTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")
        self.root.withdraw()
        # Other real-Tk tests can leave delayed callbacks in Tcl's shared
        # interpreter after destroying their root. Clear those callbacks so
        # this modal test runs in isolation instead of executing stale UI work.
        for callback_id in self.root.tk.splitlist(
            self.root.tk.call("after", "info")
        ):
            self.root.after_cancel(callback_id)
        self.addCleanup(self.root.destroy)

    @staticmethod
    def descendants(widget):
        result = []
        for child in widget.winfo_children():
            result.append(child)
            result.extend(MultipleCsvImportDialogTests.descendants(child))
        return result

    def test_dialog_shows_explicit_choices_and_duplicate_warning(self):
        app = LegoVerificationApp.__new__(LegoVerificationApp)
        app.root = self.root
        captured = {}

        def inspect_and_choose_add():
            dialog = next(
                child
                for child in self.root.winfo_children()
                if isinstance(child, tk.Toplevel)
            )
            widgets = self.descendants(dialog)
            captured["texts"] = [
                str(widget.cget("text"))
                for widget in widgets
                if "text" in widget.keys()
            ]
            next(
                widget
                for widget in widgets
                if (
                    "text" in widget.keys()
                    and widget.cget("text") == "Add as another scan batch"
                )
            ).invoke()

        self.root.after(25, inspect_and_choose_add)
        mode = app._choose_csv_import_mode(
            csv_path="duplicate.csv",
            summary=summary(2, 1),
            impact={
                "incoming_quantity": 2,
                "new_rows": 0,
                "zero_rows": 0,
                "nonzero_rows": 1,
            },
            is_duplicate=True,
        )

        all_text = "\n".join(captured["texts"])
        self.assertEqual(mode, app.import_mode_add)
        self.assertIn("exact CSV content was imported before", all_text)
        self.assertIn("Add as another scan batch", captured["texts"])
        self.assertIn("Fill blanks only", captured["texts"])
        self.assertIn("Cancel", captured["texts"])


if __name__ == "__main__":
    unittest.main()
