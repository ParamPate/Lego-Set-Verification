import tempfile
import unittest
import csv
from pathlib import Path
from unittest.mock import patch

import lego_workflow


class WorkflowHelpersTests(unittest.TestCase):
    def test_segmented_folder_matches_existing_script(self):
        image = Path("C:/photos/lego.photo.png")
        self.assertEqual(
            lego_workflow.segmented_folder_for(image),
            Path("C:/photos/lego.photo_separated_parts"),
        )

    def test_transfer_preserves_source_and_avoids_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "results.csv"
            destination = root / "imports"
            source.write_text("Part ID,Color ID\n3001,5\n", encoding="utf-8")
            with patch.object(lego_workflow, "INVENTORY_IMPORTS", destination):
                first = lego_workflow.transfer_reviewed_csv(source)
                second = lego_workflow.transfer_reviewed_csv(source)
            self.assertTrue(source.exists())
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))

    def test_unapproved_rows_can_be_included_in_handoff_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "results.csv"
            destination = root / "imports"
            source.write_text(
                "Final Review Status,Final Part ID,Final BrickLink Color ID\n"
                "Ready,3001,1\n"
                "Needs Part Review,3002,2\n",
                encoding="utf-8",
            )
            original = source.read_text(encoding="utf-8")

            self.assertEqual(lego_workflow.count_unapproved_rows(source), 1)
            with patch.object(lego_workflow, "INVENTORY_IMPORTS", destination):
                copied = lego_workflow.transfer_reviewed_csv(
                    source, include_unapproved=True
                )

            with copied.open(encoding="utf-8-sig", newline="") as handle:
                statuses = [
                    row["Final Review Status"] for row in csv.DictReader(handle)
                ]
            self.assertEqual(statuses, ["Ready", "Ready"])
            self.assertEqual(source.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
