import unittest
from unittest.mock import patch

import pandas as pd

from config import BRICKOGNIZE_RESULTS_DIR, DATA_DIR
from tkinter_app import LegoVerificationApp


class ExportDialogDefaultsTests(unittest.TestCase):
    def test_rebrickable_identifiers_are_hidden_by_default(self):
        self.assertFalse(
            LegoVerificationApp._column_visible_by_default("part_num")
        )
        self.assertFalse(
            LegoVerificationApp._column_visible_by_default("color_id")
        )
        self.assertTrue(
            LegoVerificationApp._column_visible_by_default("bl_part_num")
        )
        self.assertTrue(
            LegoVerificationApp._column_visible_by_default("bl_color_id")
        )

    def test_print_family_total_aligns_with_counterpart_column(self):
        values = LegoVerificationApp._build_print_family_row_values({
            "bricklink_base_part_num": "3001",
            "bricklink_color_id": 5,
            "base_part_num": "3001",
            "color_id": 4,
            "color_name": "Red",
            "quantity_required": 3,
            "quantity_have": 2,
        })

        self.assertEqual(values[8], "")
        self.assertEqual(values[9], "Print family total")

    def test_missing_bricklink_identifiers_display_as_not_mapped(self):
        self.assertEqual(
            LegoVerificationApp._display_bricklink_identifier(""),
            "Not mapped",
        )
        self.assertEqual(
            LegoVerificationApp._display_bricklink_identifier(
                pd.NA,
                numeric=True,
            ),
            "Not mapped",
        )
        self.assertEqual(
            LegoVerificationApp._display_bricklink_identifier("6628a"),
            "6628a",
        )
        self.assertEqual(
            LegoVerificationApp._display_bricklink_identifier(5, numeric=True),
            5,
        )

    def test_missing_report_identifiers_explain_xml_exclusion(self):
        note = LegoVerificationApp._build_report_note(pd.Series({
            "quantity_missing": 2,
            "bricklink_part_num": "",
            "bricklink_color_id": pd.NA,
            "variation_note": "Related mold variation",
        }))

        self.assertIn("Related mold variation", note)
        self.assertIn(
            "Excluded from XML: BrickLink part number and color ID unavailable",
            note,
        )

    def test_non_missing_row_does_not_claim_xml_exclusion(self):
        note = LegoVerificationApp._build_report_note(pd.Series({
            "quantity_missing": 0,
            "bricklink_part_num": "",
            "bricklink_color_id": pd.NA,
            "variation_note": "",
        }))

        self.assertEqual(note, "")

    def test_ambiguous_part_note_lists_candidates_until_manually_resolved(self):
        ambiguous_row = pd.Series({
            "quantity_missing": 1,
            "bricklink_part_num": "",
            "bricklink_part_candidates": '["3001a", "3001b"]',
            "bricklink_color_id": 5,
            "variation_note": "",
        })
        resolved_row = ambiguous_row.copy()
        resolved_row["bricklink_part_num"] = "3001b"

        ambiguous_note = LegoVerificationApp._build_report_note(ambiguous_row)
        resolved_note = LegoVerificationApp._build_report_note(resolved_row)

        self.assertIn("Multiple BrickLink IDs found", ambiguous_note)
        self.assertIn("3001a, 3001b", ambiguous_note)
        self.assertNotIn("Multiple BrickLink IDs found", resolved_note)

    def test_brickognize_exports_use_the_dedicated_results_folder(self):
        self.assertEqual(
            BRICKOGNIZE_RESULTS_DIR,
            DATA_DIR / "brickognize_results",
        )
        self.assertTrue(BRICKOGNIZE_RESULTS_DIR.is_dir())
        self.assertTrue(
            (BRICKOGNIZE_RESULTS_DIR / "brickognize_results.csv").is_file()
        )
        self.assertTrue(
            (BRICKOGNIZE_RESULTS_DIR / "brickognize_results_2.csv").is_file()
        )
        self.assertFalse((DATA_DIR / "brickognize_results.csv").exists())
        self.assertFalse((DATA_DIR / "brickognize_results_2.csv").exists())

    def test_import_dialog_opens_in_the_results_folder(self):
        app = object.__new__(LegoVerificationApp)
        app.root = object()
        app.parts_df = object()
        app.workflow_stage = app.parts_entry_stage

        with patch(
            "tkinter_app.filedialog.askopenfilename",
            return_value="",
        ) as open_dialog:
            app.prefill_csv()

        self.assertEqual(
            open_dialog.call_args.kwargs["initialdir"],
            BRICKOGNIZE_RESULTS_DIR,
        )

    def test_export_defaults_to_timestamped_wanted_list_xml(self):
        app = object.__new__(LegoVerificationApp)
        app.root = object()
        app.report_df = object()
        app.set_num = "75375/1"

        with (
            patch(
                "tkinter_app.time.strftime",
                return_value="2026-08-07_14-32-05",
            ),
            patch(
                "tkinter_app.filedialog.asksaveasfilename",
                return_value="",
            ) as save_dialog,
        ):
            app.export_current_report()

        dialog_options = save_dialog.call_args.kwargs
        self.assertEqual(
            dialog_options["initialfile"],
            "75375-1_2026-08-07_14-32-05.xml",
        )
        self.assertEqual(dialog_options["defaultextension"], ".xml")
        self.assertEqual(
            dialog_options["filetypes"][0],
            ("BrickLink Wanted List XML", "*.xml"),
        )


if __name__ == "__main__":
    unittest.main()
