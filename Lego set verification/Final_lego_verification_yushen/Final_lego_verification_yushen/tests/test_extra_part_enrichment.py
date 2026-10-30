import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.api_rebrickable import RebrickableAPIError
from src.storage import load_job, save_job
from tkinter_app import LegoVerificationApp


def comparison_rows():
    return pd.DataFrame([
        {
            "part_num": "required",
            "part_name": "Required part",
            "color_id": 1,
            "color_name": "White",
            "image_url": "https://example.test/required-white.png",
            "status": "Missing",
        },
        {
            "part_num": "extra",
            "part_name": pd.NA,
            "color_id": 5,
            "color_name": pd.NA,
            "image_url": pd.NA,
            "status": "Not in set",
        },
    ])


class ExtraPartEnrichmentTests(unittest.TestCase):
    @patch("tkinter_app.get_part_colors_df")
    @patch("tkinter_app.get_parts_bulk_df")
    def test_enriches_name_and_exact_color_image(
        self,
        bulk_lookup,
        color_lookup,
    ):
        bulk_lookup.return_value = pd.DataFrame([{
            "part_num": "extra",
            "part_name": "Extra wheel",
            # The bulk image may be a different color and must not be used.
            "image_url": "https://example.test/generic-blue.png",
        }])
        color_lookup.return_value = pd.DataFrame([
            {
                "part_num": "extra",
                "color_id": 2,
                "color_name": "Blue",
                "image_url": "https://example.test/extra-blue.png",
            },
            {
                "part_num": "extra",
                "color_id": 5,
                "color_name": "Red",
                "image_url": "https://example.test/extra-red.png",
            },
        ])

        app = object.__new__(LegoVerificationApp)
        enriched, metadata_df = app._enrich_not_in_set_parts(
            comparison_rows()
        )
        extra_row = enriched[enriched["status"] == "Not in set"].iloc[0]

        bulk_lookup.assert_called_once_with(["extra"])
        color_lookup.assert_called_once_with("extra")
        self.assertEqual(extra_row["part_name"], "Extra wheel")
        self.assertEqual(extra_row["color_name"], "Red")
        self.assertEqual(
            extra_row["image_url"],
            "https://example.test/extra-red.png",
        )
        self.assertEqual(len(metadata_df), 1)
        self.assertEqual(
            metadata_df.iloc[0]["image_url"],
            "https://example.test/extra-red.png",
        )

    @patch("tkinter_app.get_part_colors_df")
    @patch("tkinter_app.get_parts_bulk_df")
    def test_does_not_use_an_image_from_the_wrong_color(
        self,
        bulk_lookup,
        color_lookup,
    ):
        bulk_lookup.return_value = pd.DataFrame([{
            "part_num": "extra",
            "part_name": "Extra wheel",
            "image_url": "https://example.test/generic-blue.png",
        }])
        color_lookup.return_value = pd.DataFrame([{
            "part_num": "extra",
            "color_id": 2,
            "color_name": "Blue",
            "image_url": "https://example.test/extra-blue.png",
        }])

        app = object.__new__(LegoVerificationApp)
        enriched, metadata_df = app._enrich_not_in_set_parts(
            comparison_rows()
        )
        extra_row = enriched[enriched["status"] == "Not in set"].iloc[0]

        self.assertEqual(extra_row["part_name"], "Extra wheel")
        self.assertEqual(extra_row["color_name"], "")
        self.assertEqual(extra_row["image_url"], "")
        # A successful exact-color lookup with no match is a reusable result.
        self.assertEqual(len(metadata_df), 1)

    @patch("tkinter_app.get_part_colors_df")
    @patch("tkinter_app.get_parts_bulk_df")
    def test_lookup_failure_does_not_block_comparison(
        self,
        bulk_lookup,
        color_lookup,
    ):
        bulk_lookup.side_effect = RebrickableAPIError("offline")
        color_lookup.side_effect = RebrickableAPIError("offline")

        app = object.__new__(LegoVerificationApp)
        enriched, metadata_df = app._enrich_not_in_set_parts(
            comparison_rows()
        )
        extra_row = enriched[enriched["status"] == "Not in set"].iloc[0]

        self.assertEqual(extra_row["part_name"], "")
        self.assertEqual(extra_row["color_name"], "")
        self.assertEqual(extra_row["image_url"], "")
        self.assertTrue(metadata_df.empty)

    @patch("tkinter_app.get_part_colors_df")
    @patch("tkinter_app.get_parts_bulk_df")
    def test_reuses_saved_metadata_without_api_calls(
        self,
        bulk_lookup,
        color_lookup,
    ):
        saved_metadata_df = pd.DataFrame([{
            "part_num": "extra",
            "color_id": 5,
            "part_name": "Saved wheel",
            "color_name": "Red",
            "image_url": "https://example.test/saved-red.png",
        }])

        app = object.__new__(LegoVerificationApp)
        enriched, current_metadata_df = app._enrich_not_in_set_parts(
            comparison_rows(),
            saved_metadata_df,
        )
        extra_row = enriched[enriched["status"] == "Not in set"].iloc[0]

        bulk_lookup.assert_not_called()
        color_lookup.assert_not_called()
        self.assertEqual(extra_row["part_name"], "Saved wheel")
        self.assertEqual(extra_row["color_name"], "Red")
        self.assertEqual(
            extra_row["image_url"],
            "https://example.test/saved-red.png",
        )
        self.assertEqual(len(current_metadata_df), 1)

    @patch("tkinter_app.get_part_colors_df")
    @patch("tkinter_app.get_parts_bulk_df")
    def test_fetches_only_new_keys_and_prunes_stale_metadata(
        self,
        bulk_lookup,
        color_lookup,
    ):
        rows = pd.concat([
            comparison_rows(),
            pd.DataFrame([{
                "part_num": "new_extra",
                "part_name": pd.NA,
                "color_id": 7,
                "color_name": pd.NA,
                "image_url": pd.NA,
                "status": "Not in set",
            }]),
        ], ignore_index=True)
        saved_metadata_df = pd.DataFrame([
            {
                "part_num": "extra",
                "color_id": 5,
                "part_name": "Saved wheel",
                "color_name": "Red",
                "image_url": "https://example.test/saved-red.png",
            },
            {
                "part_num": "stale_extra",
                "color_id": 9,
                "part_name": "No longer present",
                "color_name": "Gray",
                "image_url": "https://example.test/stale.png",
            },
        ])
        bulk_lookup.return_value = pd.DataFrame([{
            "part_num": "new_extra",
            "part_name": "New extra part",
        }])
        color_lookup.return_value = pd.DataFrame([{
            "part_num": "new_extra",
            "color_id": 7,
            "color_name": "Green",
            "image_url": "https://example.test/new-green.png",
        }])

        app = object.__new__(LegoVerificationApp)
        enriched, current_metadata_df = app._enrich_not_in_set_parts(
            rows,
            saved_metadata_df,
        )

        bulk_lookup.assert_called_once_with(["new_extra"])
        color_lookup.assert_called_once_with("new_extra")
        metadata_keys = {
            (str(row["part_num"]), int(row["color_id"]))
            for _, row in current_metadata_df.iterrows()
        }
        self.assertEqual(
            metadata_keys,
            {("extra", 5), ("new_extra", 7)},
        )
        new_row = enriched[enriched["part_num"] == "new_extra"].iloc[0]
        self.assertEqual(new_row["part_name"], "New extra part")
        self.assertEqual(
            new_row["image_url"],
            "https://example.test/new-green.png",
        )

    def test_extra_metadata_round_trips_through_saved_job(self):
        user_input_df = pd.DataFrame([{
            "part_num": "extra",
            "color_id": 5,
            "quantity_have": 1,
        }])
        metadata_df = pd.DataFrame([{
            "part_num": "extra",
            "color_id": 5,
            "part_name": "Extra wheel",
            "color_name": "Red",
            "image_url": "https://example.test/extra-red.png",
        }])

        with tempfile.TemporaryDirectory() as temp_dir:
            save_job(
                Path(temp_dir),
                "job",
                "1234",
                user_input_df,
                status="complete",
                extra_part_metadata_df=metadata_df,
            )
            loaded = load_job(Path(temp_dir), "job")

        loaded_metadata_df = loaded["extra_part_metadata_df"]
        self.assertEqual(len(loaded_metadata_df), 1)
        self.assertEqual(
            loaded_metadata_df.iloc[0]["image_url"],
            "https://example.test/extra-red.png",
        )


if __name__ == "__main__":
    unittest.main()
