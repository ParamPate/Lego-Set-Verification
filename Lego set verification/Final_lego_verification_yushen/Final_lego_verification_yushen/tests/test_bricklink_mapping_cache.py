import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.api_rebrickable import (
    RebrickableAPIError,
    get_parts_external_ids_df,
)
from src.bricklink_mapping import (
    AMBIGUOUS_MATCH,
    ASSUMED_MATCH,
    BRICKLINK_MATCH,
    DIRECT_MATCH,
    EXTERNAL_ID_MATCH,
    MANUAL_MATCH,
    UNRESOLVED,
    load_part_map,
    parse_bricklink_candidates,
    part_map_columns,
    resolve_bricklink_part_nums,
    resolve_rebrickable_part_nums,
    save_manual_part_mapping,
    save_part_map,
)


class RebrickableToBrickLinkCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.cache_path = Path(self.temp_dir.name) / "part_map.csv"
        self.path_patch = patch(
            "src.bricklink_mapping.part_map_csv_path",
            self.cache_path,
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_interrupted_part_map_write_preserves_existing_cache(self):
        self.cache_path.write_text("existing cache", encoding="utf-8")

        class InterruptedDataFrame(pd.DataFrame):
            def to_csv(self, destination, *args, **kwargs):
                if hasattr(destination, "write"):
                    destination.write("partial replacement")
                else:
                    Path(destination).write_text(
                        "partial replacement",
                        encoding="utf-8",
                    )
                raise OSError("interrupted write")

        with self.assertRaisesRegex(OSError, "interrupted write"):
            save_part_map(InterruptedDataFrame(columns=part_map_columns))

        self.assertEqual(
            self.cache_path.read_text(encoding="utf-8"),
            "existing cache",
        )
        self.assertEqual(list(self.cache_path.parent.glob("*.tmp")), [])

    def test_api_failure_propagates_without_creating_assumed_cache_row(self):
        with patch(
            "src.bricklink_mapping.get_parts_external_ids_df",
            side_effect=RebrickableAPIError("offline"),
        ):
            with self.assertRaisesRegex(RebrickableAPIError, "offline"):
                resolve_rebrickable_part_nums(["3626cpr0816"])

        self.assertFalse(self.cache_path.exists())

    def test_failed_lookup_is_retried_and_cached_after_recovery(self):
        recovered_df = pd.DataFrame([
            {
                "part_num": "3626cpr0816",
                "part_name": "Minifig Head",
                "bricklink_part_num": "3626pb0634",
            }
        ])

        with patch(
            "src.bricklink_mapping.get_parts_external_ids_df",
            side_effect=[
                RebrickableAPIError("rate limited"),
                recovered_df,
            ],
        ) as lookup:
            with self.assertRaises(RebrickableAPIError):
                resolve_rebrickable_part_nums(["3626cpr0816"])

            result_df = resolve_rebrickable_part_nums(["3626cpr0816"])

        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(result_df.iloc[0]["bricklink_part_num"], "3626pb0634")
        self.assertEqual(result_df.iloc[0]["resolved_via"], EXTERNAL_ID_MATCH)
        cached_df = load_part_map()
        self.assertEqual(cached_df.iloc[-1]["bricklink_part_num"], "3626pb0634")

    def test_successful_no_external_id_result_is_cached_as_verified_assumption(self):
        no_external_id_df = pd.DataFrame([
            {
                "part_num": "3001",
                "part_name": "Brick 2 x 4",
                "bricklink_part_num": "",
            }
        ])

        with patch(
            "src.bricklink_mapping.get_parts_external_ids_df",
            return_value=no_external_id_df,
        ) as lookup:
            first_df = resolve_rebrickable_part_nums(["3001"])
            second_df = resolve_rebrickable_part_nums(["3001"])

        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(first_df.iloc[0]["bricklink_part_num"], "3001")
        self.assertEqual(first_df.iloc[0]["resolved_via"], ASSUMED_MATCH)
        self.assertEqual(second_df.iloc[0]["resolved_via"], ASSUMED_MATCH)

    def test_multiple_external_ids_are_preserved_without_selecting_first(self):
        api_result = [{
            "part_num": "multi-part",
            "name": "Ambiguous printed part",
            "external_ids": {
                "BrickLink": ["candidate-a", "candidate-b", "candidate-a"],
            },
        }]

        with patch(
            "src.api_rebrickable.get_parts_bulk",
            return_value=api_result,
        ):
            result_df = get_parts_external_ids_df(["multi-part"])

        self.assertEqual(result_df.iloc[0]["bricklink_part_num"], "")
        self.assertEqual(
            result_df.iloc[0]["bricklink_part_candidates"],
            ["candidate-a", "candidate-b"],
        )

    def test_ambiguous_mapping_is_cached_without_becoming_exportable(self):
        ambiguous_df = pd.DataFrame([{
            "part_num": "multi-part",
            "part_name": "Ambiguous printed part",
            "bricklink_part_num": "",
            "bricklink_part_candidates": ["candidate-a", "candidate-b"],
        }])

        with patch(
            "src.bricklink_mapping.get_parts_external_ids_df",
            return_value=ambiguous_df,
        ) as lookup:
            first_df = resolve_rebrickable_part_nums(["multi-part"])
            second_df = resolve_rebrickable_part_nums(["multi-part"])

        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(first_df.iloc[0]["bricklink_part_num"], "")
        self.assertEqual(first_df.iloc[0]["resolved_via"], AMBIGUOUS_MATCH)
        self.assertEqual(
            parse_bricklink_candidates(
                first_df.iloc[0]["bricklink_part_candidates"]
            ),
            ["candidate-a", "candidate-b"],
        )
        self.assertEqual(second_df.iloc[0]["resolved_via"], AMBIGUOUS_MATCH)

    def test_manual_selection_replaces_ambiguity_for_future_lookups(self):
        ambiguous_df = pd.DataFrame([{
            "part_num": "multi-part",
            "part_name": "Ambiguous printed part",
            "bricklink_part_num": "",
            "bricklink_part_candidates": ["candidate-a", "candidate-b"],
        }])

        with patch(
            "src.bricklink_mapping.get_parts_external_ids_df",
            return_value=ambiguous_df,
        ) as lookup:
            resolve_rebrickable_part_nums(["multi-part"])
            save_manual_part_mapping(
                "multi-part",
                "candidate-b",
                part_name="Ambiguous printed part",
                candidates=["candidate-a", "candidate-b"],
            )
            resolved_df = resolve_rebrickable_part_nums(["multi-part"])

        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(
            resolved_df.iloc[0]["bricklink_part_num"],
            "candidate-b",
        )
        self.assertEqual(resolved_df.iloc[0]["resolved_via"], MANUAL_MATCH)

    def test_legacy_part_map_without_candidates_column_still_loads(self):
        pd.DataFrame([{
            "bricklink_part_num": "3001",
            "rebrickable_part_num": "3001",
            "part_name": "Brick 2 x 4",
            "resolved_via": DIRECT_MATCH,
        }]).to_csv(self.cache_path, index=False, encoding="utf-8")

        loaded_df = load_part_map()

        self.assertEqual(loaded_df.iloc[0]["bricklink_part_num"], "3001")
        self.assertEqual(loaded_df.iloc[0]["bricklink_part_candidates"], "")

    def test_omitted_part_is_not_treated_as_confirmed_assumption(self):
        incomplete_df = pd.DataFrame(
            columns=["part_num", "part_name", "bricklink_part_num"]
        )

        with patch(
            "src.bricklink_mapping.get_parts_external_ids_df",
            return_value=incomplete_df,
        ):
            with self.assertRaisesRegex(
                RebrickableAPIError,
                "did not return part details",
            ):
                resolve_rebrickable_part_nums(["3626cpr0816"])

        self.assertFalse(self.cache_path.exists())

    def test_legacy_assumed_cache_row_is_retried_without_manual_deletion(self):
        save_part_map(pd.DataFrame([
            {
                "bricklink_part_num": "3626cpr0816",
                "rebrickable_part_num": "3626cpr0816",
                "part_name": "",
                "resolved_via": "assumed",
            }
        ], columns=part_map_columns))
        recovered_df = pd.DataFrame([
            {
                "part_num": "3626cpr0816",
                "part_name": "Minifig Head",
                "bricklink_part_num": "3626pb0634",
            }
        ])

        with patch(
            "src.bricklink_mapping.get_parts_external_ids_df",
            return_value=recovered_df,
        ) as lookup:
            result_df = resolve_rebrickable_part_nums(["3626cpr0816"])

        lookup.assert_called_once_with(["3626cpr0816"])
        self.assertEqual(result_df.iloc[0]["bricklink_part_num"], "3626pb0634")
        self.assertEqual(result_df.iloc[0]["resolved_via"], EXTERNAL_ID_MATCH)


class BrickLinkToRebrickableCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.cache_path = Path(self.temp_dir.name) / "part_map.csv"
        self.path_patch = patch(
            "src.bricklink_mapping.part_map_csv_path",
            self.cache_path,
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_bulk_failure_is_retried_and_direct_match_is_cached(self):
        recovered_df = pd.DataFrame([
            {"part_num": "3001", "part_name": "Brick 2 x 4"}
        ])

        with patch(
            "src.bricklink_mapping.get_parts_bulk_df",
            side_effect=[RebrickableAPIError("offline"), recovered_df],
        ) as bulk_lookup, patch(
            "src.bricklink_mapping.find_part_by_bricklink_id"
        ) as fallback_lookup:
            with self.assertRaisesRegex(RebrickableAPIError, "offline"):
                resolve_bricklink_part_nums(["3001"])
            self.assertFalse(self.cache_path.exists())

            result_df = resolve_bricklink_part_nums(["3001"])

        self.assertEqual(bulk_lookup.call_count, 2)
        fallback_lookup.assert_not_called()
        self.assertEqual(result_df.iloc[0]["rebrickable_part_num"], "3001")
        self.assertEqual(result_df.iloc[0]["resolved_via"], DIRECT_MATCH)

    def test_fallback_failure_does_not_cache_partial_batch_and_is_retried(self):
        direct_df = pd.DataFrame([
            {"part_num": "3001", "part_name": "Brick 2 x 4"}
        ])
        recovered_match = {
            "part_num": "3626pr0816",
            "part_name": "Minifig Head",
        }

        with patch(
            "src.bricklink_mapping.get_parts_bulk_df",
            return_value=direct_df,
        ) as bulk_lookup, patch(
            "src.bricklink_mapping.find_part_by_bricklink_id",
            side_effect=[RebrickableAPIError("rate limited"), recovered_match],
        ) as fallback_lookup:
            with self.assertRaisesRegex(RebrickableAPIError, "rate limited"):
                resolve_bricklink_part_nums(["3001", "3626pb0816"])
            self.assertFalse(self.cache_path.exists())

            result_df = resolve_bricklink_part_nums(
                ["3001", "3626pb0816"]
            )

        self.assertEqual(bulk_lookup.call_count, 2)
        self.assertEqual(fallback_lookup.call_count, 2)
        resolved_lookup = result_df.set_index("bricklink_part_num")
        self.assertEqual(
            resolved_lookup.loc["3001", "resolved_via"],
            DIRECT_MATCH,
        )
        self.assertEqual(
            resolved_lookup.loc["3626pb0816", "rebrickable_part_num"],
            "3626pr0816",
        )
        self.assertEqual(
            resolved_lookup.loc["3626pb0816", "resolved_via"],
            BRICKLINK_MATCH,
        )

    def test_successful_no_match_is_cached_as_verified_unresolved(self):
        with patch(
            "src.bricklink_mapping.get_parts_bulk_df",
            return_value=pd.DataFrame(),
        ) as bulk_lookup, patch(
            "src.bricklink_mapping.find_part_by_bricklink_id",
            return_value=None,
        ) as fallback_lookup:
            first_df = resolve_bricklink_part_nums(["not-a-real-part"])
            second_df = resolve_bricklink_part_nums(["not-a-real-part"])

        self.assertEqual(bulk_lookup.call_count, 1)
        self.assertEqual(fallback_lookup.call_count, 1)
        self.assertEqual(first_df.iloc[0]["resolved_via"], UNRESOLVED)
        self.assertEqual(second_df.iloc[0]["resolved_via"], UNRESOLVED)
        self.assertEqual(first_df.iloc[0]["rebrickable_part_num"], "")

    def test_legacy_unresolved_cache_row_is_retried_without_manual_deletion(self):
        save_part_map(pd.DataFrame([
            {
                "bricklink_part_num": "67095pb067",
                "rebrickable_part_num": "",
                "part_name": "",
                "resolved_via": "unresolved",
            }
        ], columns=part_map_columns))
        recovered_match = {
            "part_num": "67095pr0001",
            "part_name": "Minifig Torso",
        }

        with patch(
            "src.bricklink_mapping.get_parts_bulk_df",
            return_value=pd.DataFrame(),
        ) as bulk_lookup, patch(
            "src.bricklink_mapping.find_part_by_bricklink_id",
            return_value=recovered_match,
        ) as fallback_lookup:
            result_df = resolve_bricklink_part_nums(["67095pb067"])

        bulk_lookup.assert_called_once_with(
            ["67095pb067"],
            inc_part_details=True,
        )
        fallback_lookup.assert_called_once_with("67095pb067")
        self.assertEqual(
            result_df.iloc[0]["rebrickable_part_num"],
            "67095pr0001",
        )
        self.assertEqual(result_df.iloc[0]["resolved_via"], BRICKLINK_MATCH)


if __name__ == "__main__":
    unittest.main()
