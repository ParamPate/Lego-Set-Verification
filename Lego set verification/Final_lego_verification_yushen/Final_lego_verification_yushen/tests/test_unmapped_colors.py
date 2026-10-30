import io
import unittest
from unittest.mock import patch

import pandas as pd

from src.comparison import compare_parts
from src.relationships import apply_variation_substitutions
from src.user_input import fill_blank_quantities, load_from_csv


class UnmappedBrickLinkColorTests(unittest.TestCase):
    def _load_unmapped_colors(self):
        csv_data = io.StringIO(
            "Final Part ID,Final BrickLink Color ID,Final Review Status,Type\n"
            "3001,243,Ready,part\n"
            "3001,243,Ready,part\n"
            "3001,244,Ready,part\n"
        )
        empty_color_map = pd.DataFrame(columns=[
            "bricklink_color_id",
            "rebrickable_color_id",
            "bricklink_color_name",
            "rebrickable_color_name",
        ])
        part_map = pd.DataFrame([{
            "bricklink_part_num": "3001",
            "rebrickable_part_num": "3001",
            "part_name": "Brick 2 x 4",
            "resolved_via": "direct",
        }])

        with (
            patch("src.user_input.load_color_map", return_value=empty_color_map),
            patch(
                "src.user_input.resolve_bricklink_part_nums",
                return_value=part_map,
            ),
        ):
            return load_from_csv(csv_data, source_name="unmapped.csv")

    def test_import_retains_distinct_original_bricklink_color_ids(self):
        have_df, summary = self._load_unmapped_colors()

        self.assertEqual(summary.unmapped_color_ids, [243, 244])
        self.assertEqual(int(have_df["quantity_have"].sum()), 3)
        self.assertTrue(have_df["color_id"].isna().all())
        self.assertEqual(
            {
                int(row["bricklink_color_id"]): int(row["quantity_have"])
                for _, row in have_df.iterrows()
            },
            {243: 2, 244: 1},
        )

    def test_comparison_keeps_unmapped_colors_as_visible_surplus(self):
        have_df, _summary = self._load_unmapped_colors()
        required_df = pd.DataFrame(columns=[
            "part_num",
            "color_id",
            "part_name",
            "color_name",
            "image_url",
            "quantity_required",
        ])

        comparison_df = compare_parts(required_df, have_df)

        self.assertEqual(len(comparison_df), 2)
        self.assertEqual(set(comparison_df["status"]), {"Not in set"})
        self.assertEqual(int(comparison_df["quantity_have"].sum()), 3)
        self.assertEqual(
            set(comparison_df["bricklink_color_id"].astype(int)),
            {243, 244},
        )
        self.assertEqual(
            set(comparison_df["color_name"]),
            {"No Rebrickable color match"},
        )

    def test_fill_blanks_and_variation_replay_preserve_source_color(self):
        imported_df, _summary = self._load_unmapped_colors()
        current_df = pd.DataFrame([{
            "part_num": "3001",
            "color_id": pd.NA,
            "bricklink_color_id": 243,
            "quantity_have": 0,
        }])

        filled_df = fill_blank_quantities(current_df, imported_df)
        replayed_df = apply_variation_substitutions(
            filled_df,
            pd.DataFrame(),
        )

        self.assertEqual(len(replayed_df), 2)
        self.assertEqual(
            set(replayed_df["bricklink_color_id"].astype(int)),
            {243, 244},
        )
        self.assertEqual(int(replayed_df["quantity_have"].sum()), 3)


if __name__ == "__main__":
    unittest.main()
