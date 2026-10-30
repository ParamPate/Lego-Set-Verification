import unittest
from types import SimpleNamespace

import pandas as pd

from src.relationships import apply_variation_substitutions
from src.user_input import fill_blank_quantities
from tkinter_app import LegoVerificationApp


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


def build_app():
    app = LegoVerificationApp.__new__(LegoVerificationApp)
    app.user_input_df = pd.DataFrame([{
        "part_num": "target",
        "color_id": 5,
        "quantity_have": 1,
    }])
    app.parts_df = pd.DataFrame([{
        "part_num": "target",
        "color_id": 5,
        "quantity_required": 3,
    }])
    app.substitutions = []
    app.declined_substitutions = []
    app.variation_proposals = None
    app.import_have_df = None
    app.import_history = []
    app._choose_csv_import_mode = lambda **_kwargs: app.import_mode_fill
    return app


class StatusValue:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class VariationAllocationStateTests(unittest.TestCase):
    def test_finish_import_applies_allocation_and_reimport_replaces_it(self):
        app = build_app()
        raw_import = pd.DataFrame([{
            "part_num": "source",
            "color_id": 5,
            "quantity_have": 2,
        }])
        proposals_df = proposal_frame()
        summary = SimpleNamespace(
            total_quantity=2,
            distinct_combinations=1,
        )

        app.status_var = StatusValue()
        app._show_import_summary = lambda _summary: None
        app._update_review_variations_button = lambda: None
        app._render_parts_table = lambda: None
        app._invalidate_report = lambda: None
        app._review_variation_substitutions = (
            lambda proposals, **_kwargs:
                proposals.assign(quantity_allocated=2)
        )

        app._finish_csv_import(
            "parts.csv",
            (raw_import, summary, proposals_df),
        )

        first_quantities = {
            (str(row["part_num"]), int(row["color_id"])):
                int(row["quantity_have"])
            for _, row in app.user_input_df.iterrows()
        }
        self.assertEqual(first_quantities, {("target", 5): 3})
        self.assertEqual(len(app.substitutions), 1)
        self.assertEqual(app.substitutions[0]["quantity_allocated"], 2)

        app._review_variation_substitutions = (
            lambda proposals, **_kwargs: pd.DataFrame(columns=[
                *proposals.columns,
                "quantity_allocated",
            ])
        )
        app._finish_csv_import(
            "parts.csv",
            (raw_import, summary, proposals_df),
        )

        second_quantities = {
            (str(row["part_num"]), int(row["color_id"])):
                int(row["quantity_have"])
            for _, row in app.user_input_df.iterrows()
        }
        self.assertEqual(
            second_quantities,
            {
                ("target", 5): 1,
                ("source", 5): 2,
            },
        )
        self.assertEqual(app.substitutions, [])
        self.assertEqual(len(app.declined_substitutions), 1)

    def test_manual_target_quantity_reduces_shortage_and_is_preserved(self):
        app = build_app()
        raw_import = pd.DataFrame([{
            "part_num": "source",
            "color_id": 5,
            "quantity_have": 2,
        }])
        proposals_df = proposal_frame()
        baseline_df = fill_blank_quantities(app.user_input_df, raw_import)

        shortages = app._variation_target_shortages(
            proposals_df,
            baseline_have_df=baseline_df,
        )
        allocated_df = proposals_df.copy()
        allocated_df["quantity_allocated"] = 2
        result_df = apply_variation_substitutions(
            baseline_df,
            allocated_df,
            target_shortages=shortages,
        )

        self.assertEqual(shortages, {("target", 5): 2})
        self.assertEqual(len(result_df), 1)
        self.assertEqual(int(result_df.iloc[0]["quantity_have"]), 3)

    def test_re_review_restores_full_shortage_before_reallocating(self):
        app = build_app()
        raw_import = pd.DataFrame([{
            "part_num": "source",
            "color_id": 5,
            "quantity_have": 2,
        }])
        proposals_df = proposal_frame()
        baseline_df = fill_blank_quantities(app.user_input_df, raw_import)
        allocated_df = proposals_df.copy()
        allocated_df["quantity_allocated"] = 2

        app.user_input_df = apply_variation_substitutions(
            baseline_df,
            allocated_df,
            target_shortages={("target", 5): 2},
        )
        app.import_have_df = baseline_df
        app._record_substitution_choices(proposals_df, allocated_df)

        shortages = app._variation_target_shortages(
            proposals_df,
            baseline_have_df=baseline_df,
        )

        self.assertEqual(shortages, {("target", 5): 2})
        self.assertEqual(app.substitutions[0]["quantity_allocated"], 2)
        self.assertIn(
            "Counting 2 x source",
            app._substitution_labels()[("target", 5)][1],
        )

    def test_reimport_decline_does_not_count_source_twice(self):
        app = build_app()
        raw_import = pd.DataFrame([{
            "part_num": "source",
            "color_id": 5,
            "quantity_have": 2,
        }])
        proposals_df = proposal_frame()
        baseline_df = fill_blank_quantities(app.user_input_df, raw_import)
        accepted_df = proposals_df.copy()
        accepted_df["quantity_allocated"] = 2

        app.user_input_df = apply_variation_substitutions(
            baseline_df,
            accepted_df,
            target_shortages={("target", 5): 2},
        )
        app.import_have_df = baseline_df
        app.variation_proposals = proposals_df
        app._record_substitution_choices(proposals_df, accepted_df)

        replayed_decline_df = apply_variation_substitutions(
            baseline_df,
            pd.DataFrame(columns=[
                *proposals_df.columns,
                "quantity_allocated",
            ]),
            target_shortages={("target", 5): 2},
        )
        app._apply_replayed_import(proposals_df, replayed_decline_df)

        quantities = {
            (str(row["part_num"]), int(row["color_id"])):
                int(row["quantity_have"])
            for _, row in app.user_input_df.iterrows()
        }
        self.assertEqual(
            quantities,
            {
                ("target", 5): 1,
                ("source", 5): 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
