import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.relationships import (
    apply_variation_substitutions,
    find_variation_substitutions,
)
from src.storage import load_job, save_job


def quantity_lookup(dataframe):
    return {
        (str(row["part_num"]), int(row["color_id"])):
            int(row["quantity_have"])
        for _, row in dataframe.iterrows()
    }


class VariationAllocationTests(unittest.TestCase):
    def test_one_source_can_be_split_across_multiple_targets(self):
        have_df = pd.DataFrame([{
            "part_num": "source",
            "color_id": 5,
            "quantity_have": 3,
        }])
        allocations_df = pd.DataFrame([
            {
                "csv_part_num": "source",
                "csv_color_id": 5,
                "set_part_num": "target_a",
                "set_color_id": 5,
                "quantity_allocated": 1,
            },
            {
                "csv_part_num": "source",
                "csv_color_id": 5,
                "set_part_num": "target_b",
                "set_color_id": 5,
                "quantity_allocated": 1,
            },
        ])

        result_df = apply_variation_substitutions(
            have_df,
            allocations_df,
            target_shortages={
                ("target_a", 5): 1,
                ("target_b", 5): 2,
            },
        )

        self.assertEqual(
            quantity_lookup(result_df),
            {
                ("source", 5): 1,
                ("target_a", 5): 1,
                ("target_b", 5): 1,
            },
        )

    def test_fully_allocated_source_row_is_removed(self):
        have_df = pd.DataFrame([{
            "part_num": "source",
            "color_id": 5,
            "quantity_have": 2,
        }])
        allocations_df = pd.DataFrame([{
            "csv_part_num": "source",
            "csv_color_id": 5,
            "set_part_num": "target",
            "set_color_id": 5,
            "quantity_allocated": 2,
        }])

        result_df = apply_variation_substitutions(
            have_df,
            allocations_df,
            target_shortages={("target", 5): 2},
        )

        self.assertEqual(
            quantity_lookup(result_df),
            {("target", 5): 2},
        )

    def test_source_overallocation_is_rejected_atomically(self):
        have_df = pd.DataFrame([{
            "part_num": "source",
            "color_id": 5,
            "quantity_have": 2,
        }])
        allocations_df = pd.DataFrame([
            {
                "csv_part_num": "source",
                "csv_color_id": 5,
                "set_part_num": "target_a",
                "set_color_id": 5,
                "quantity_allocated": 2,
            },
            {
                "csv_part_num": "source",
                "csv_color_id": 5,
                "set_part_num": "target_b",
                "set_color_id": 5,
                "quantity_allocated": 1,
            },
        ])

        with self.assertRaisesRegex(ValueError, "only 2 is available"):
            apply_variation_substitutions(
                have_df,
                allocations_df,
                target_shortages={
                    ("target_a", 5): 2,
                    ("target_b", 5): 1,
                },
            )

        self.assertEqual(
            quantity_lookup(have_df),
            {("source", 5): 2},
        )

    def test_allocations_from_multiple_sources_share_target_shortage(self):
        have_df = pd.DataFrame([
            {"part_num": "source_a", "color_id": 5, "quantity_have": 1},
            {"part_num": "source_b", "color_id": 5, "quantity_have": 1},
        ])
        allocations_df = pd.DataFrame([
            {
                "csv_part_num": "source_a",
                "csv_color_id": 5,
                "set_part_num": "target",
                "set_color_id": 5,
                "quantity_allocated": 1,
            },
            {
                "csv_part_num": "source_b",
                "csv_color_id": 5,
                "set_part_num": "target",
                "set_color_id": 5,
                "quantity_allocated": 1,
            },
        ])

        with self.assertRaisesRegex(ValueError, "remaining shortage is 1"):
            apply_variation_substitutions(
                have_df,
                allocations_df,
                target_shortages={("target", 5): 1},
            )

    def test_fractional_allocation_is_rejected(self):
        have_df = pd.DataFrame([{
            "part_num": "source",
            "color_id": 5,
            "quantity_have": 1,
        }])
        allocations_df = pd.DataFrame([{
            "csv_part_num": "source",
            "csv_color_id": 5,
            "set_part_num": "target",
            "set_color_id": 5,
            "quantity_allocated": 0.5,
        }])

        with self.assertRaisesRegex(ValueError, "whole number"):
            apply_variation_substitutions(have_df, allocations_df)

    def test_missing_source_is_rejected_instead_of_silently_skipped(self):
        have_df = pd.DataFrame([{
            "part_num": "different_source",
            "color_id": 5,
            "quantity_have": 1,
        }])
        allocations_df = pd.DataFrame([{
            "csv_part_num": "source",
            "csv_color_id": 5,
            "set_part_num": "target",
            "set_color_id": 5,
            "quantity_allocated": 1,
        }])

        with self.assertRaisesRegex(ValueError, "is not on hand"):
            apply_variation_substitutions(have_df, allocations_df)

    def test_legacy_single_target_choice_still_moves_full_quantity(self):
        have_df = pd.DataFrame([{
            "part_num": "source",
            "color_id": 5,
            "quantity_have": 2,
        }])
        legacy_accepted_df = pd.DataFrame([{
            "csv_part_num": "source",
            "csv_color_id": 5,
            "quantity_have": 2,
            "set_part_num": "target",
            "set_color_id": 5,
        }])

        result_df = apply_variation_substitutions(
            have_df,
            legacy_accepted_df,
        )

        self.assertEqual(
            quantity_lookup(result_df),
            {("target", 5): 2},
        )

    def test_proposals_include_target_required_quantity(self):
        have_df = pd.DataFrame([{
            "part_num": "source",
            "color_id": 5,
            "quantity_have": 2,
        }])
        required_df = pd.DataFrame([{
            "part_num": "target",
            "part_name": "Target",
            "color_id": 5,
            "color_name": "Red",
            "quantity_required": 3,
        }])
        relationships_df = pd.DataFrame([{
            "rel_type": "M",
            "child_part_num": "source",
            "parent_part_num": "target",
        }])

        proposals_df = find_variation_substitutions(
            have_df,
            required_df,
            relationships_df,
        )

        self.assertEqual(len(proposals_df), 1)
        self.assertEqual(int(proposals_df.iloc[0]["quantity_required"]), 3)

    def test_allocation_fields_round_trip_through_saved_job(self):
        user_input_df = pd.DataFrame([{
            "part_num": "target",
            "color_id": 5,
            "quantity_have": 1,
        }])
        proposals_df = pd.DataFrame([{
            "csv_part_num": "source",
            "csv_color_id": 5,
            "quantity_have": 2,
            "set_part_num": "target",
            "set_color_id": 5,
            "quantity_required": 1,
        }])
        substitution = {
            "csv_part_num": "source",
            "csv_color_id": 5,
            "quantity_have": 2,
            "set_part_num": "target",
            "set_color_id": 5,
            "quantity_allocated": 1,
            "rel_type": "M",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            save_job(
                Path(temp_dir),
                "job",
                "1234",
                user_input_df,
                substitutions=[substitution],
                variation_proposals_df=proposals_df,
                import_have_df=user_input_df,
            )
            loaded = load_job(Path(temp_dir), "job")

        self.assertEqual(
            loaded["substitutions"][0]["quantity_allocated"],
            1,
        )
        self.assertEqual(
            int(loaded["variation_proposals_df"].iloc[0]["quantity_required"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
