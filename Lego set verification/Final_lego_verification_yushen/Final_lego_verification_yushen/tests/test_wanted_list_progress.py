import unittest
import xml.etree.ElementTree as ET

import pandas as pd

from src.comparison import (
    build_full_report,
    report_to_bricklink_xml_bytes,
    select_wanted_list_rows,
)


def comparison_rows():
    return pd.DataFrame([
        {
            "bricklink_part_num": "6628a",
            "bricklink_color_id": 5,
            "part_num": "66906",
            "quantity_required": 1,
            "quantity_have": 1,
            "quantity_missing": 0,
            "quantity_extra": 0,
            "status": "Complete",
        },
        {
            "bricklink_part_num": "87552",
            "bricklink_color_id": 11,
            "part_num": "87552",
            "quantity_required": 2,
            "quantity_have": 1,
            "quantity_missing": 1,
            "quantity_extra": 0,
            "status": "Missing",
        },
        {
            "bricklink_part_num": "3001",
            "bricklink_color_id": 1,
            "part_num": "3001",
            "quantity_required": 1,
            "quantity_have": 2,
            "quantity_missing": 0,
            "quantity_extra": 1,
            "status": "Extra",
        },
        {
            "bricklink_part_num": "6628",
            "bricklink_color_id": 5,
            "part_num": "6628",
            "quantity_required": 0,
            "quantity_have": 1,
            "quantity_missing": 0,
            "quantity_extra": 1,
            "status": "Not in set",
        },
    ])


class WantedListMissingPartsTests(unittest.TestCase):
    def test_xml_contains_only_missing_quantities(self):
        xml_bytes = report_to_bricklink_xml_bytes(comparison_rows())

        self.assertFalse(xml_bytes.startswith(b"<?xml"))
        root = ET.fromstring(xml_bytes)
        items = {
            item.findtext("ITEMID"): item
            for item in root.findall("ITEM")
        }

        self.assertEqual(set(items), {"87552"})
        missing_item = items["87552"]
        self.assertEqual(missing_item.findtext("MINQTY"), "1")
        self.assertIsNone(missing_item.find("QTYFILLED"))
        self.assertEqual(missing_item.findtext("CONDITION"), "U")
        self.assertIsNotNone(missing_item.find("REMARKS"))
        self.assertIn(missing_item.findtext("REMARKS"), {None, ""})
        self.assertNotIn(b"75375", xml_bytes)

    def test_csv_report_remains_attention_only(self):
        report_df = build_full_report(comparison_rows())

        self.assertNotIn("66906", set(report_df["part_num"]))
        self.assertEqual(
            set(report_df["part_num"]),
            {"87552", "3001", "6628"},
        )

    def test_operator_remark_is_preserved_in_csv_and_escaped_in_xml(self):
        comparison_df = comparison_rows()
        missing_mask = comparison_df["part_num"].eq("87552")
        comparison_df.loc[
            missing_mask,
            "wanted_list_remark",
        ] = "Bag 3 & <fragile>"

        report_df = build_full_report(comparison_df)
        missing_report_row = report_df[
            report_df["part_num"].eq("87552")
        ].iloc[0]
        self.assertEqual(
            missing_report_row["wanted_list_remark"],
            "Bag 3 & <fragile>",
        )

        xml_bytes = report_to_bricklink_xml_bytes(comparison_df)
        missing_item = ET.fromstring(xml_bytes).find("ITEM")
        self.assertEqual(
            missing_item.findtext("REMARKS"),
            "Bag 3 & <fragile>",
        )
        self.assertIn(b"Bag 3 &amp; &lt;fragile&gt;", xml_bytes)

    def test_untranslated_missing_parts_are_returned_as_skipped(self):
        comparison_df = comparison_rows()
        comparison_df.loc[
            comparison_df["part_num"] == "87552",
            "bricklink_part_num",
        ] = ""

        exportable, skipped = select_wanted_list_rows(comparison_df)

        self.assertTrue(exportable.empty)
        self.assertEqual(set(skipped["part_num"]), {"87552"})

    def test_ambiguous_missing_part_is_skipped_with_candidates_preserved(self):
        comparison_df = comparison_rows()
        missing_mask = comparison_df["part_num"].eq("87552")
        comparison_df.loc[missing_mask, "bricklink_part_num"] = ""
        comparison_df.loc[
            missing_mask,
            "bricklink_part_candidates",
        ] = '["87552a", "87552b"]'

        exportable, skipped = select_wanted_list_rows(comparison_df)

        self.assertTrue(exportable.empty)
        self.assertEqual(
            skipped.iloc[0]["bricklink_part_candidates"],
            '["87552a", "87552b"]',
        )

    def test_complete_extra_and_not_in_set_rows_are_not_selected(self):
        exportable, skipped = select_wanted_list_rows(comparison_rows())

        self.assertEqual(set(exportable["part_num"]), {"87552"})
        self.assertTrue(skipped.empty)

    def test_xml_raises_when_nothing_is_missing(self):
        comparison_df = comparison_rows()
        comparison_df["quantity_missing"] = 0

        with self.assertRaisesRegex(ValueError, "No missing parts"):
            report_to_bricklink_xml_bytes(comparison_df)


if __name__ == "__main__":
    unittest.main()
