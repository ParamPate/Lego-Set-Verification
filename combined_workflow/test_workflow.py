import tempfile
import unittest
import csv
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace

import lego_workflow


class WorkflowHelpersTests(unittest.TestCase):

    def test_recognition_uses_mac_python_and_keeps_windows_environment(self):
        for platform, expected in (("darwin", lego_workflow.sys.executable),
                                   ("win32", str(lego_workflow.RECOGNITION_PYTHON))):
            with self.subTest(platform=platform):
                app = SimpleNamespace(_start_process=Mock())
                with patch.object(lego_workflow.sys, "platform", platform), patch.object(Path, "is_file", return_value=True):
                    lego_workflow.LegoWorkflowApp.launch_recognition(app)
                self.assertEqual(app._start_process.call_args.args[0][0], expected)

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
            source.write_text(
                "Final Review Status,Final Part ID,Final BrickLink Color ID\n"
                "Ready,3001,5\n", encoding="utf-8",
            )
            with patch.object(lego_workflow, "INVENTORY_IMPORTS", destination):
                first = lego_workflow.transfer_reviewed_csv(source)
                second = lego_workflow.transfer_reviewed_csv(source)
            self.assertTrue(source.exists())
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(encoding="utf-8-sig"), source.read_text(encoding="utf-8"))

    def test_default_handoff_counts_each_ready_complete_part_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "results.csv"
            source.write_text(
                "Image,Final Review Status,Final Part ID,Final BrickLink Color ID,Type\n"
                "a.png,Ready,3001,5,part\n"
                "b.png,Ready,3001,5,part\n"
                "c.png,Needs Color Review,3001,5,part\n"
                "d.png,Ready,,5,part\n"
                "e.png,Ready,3001,,part\n"
                "f.png,Ready,3001,5,set\n"
                "g.png,Ready,Unknown,5,part\n"
                "h.png,Ready,3001,invalid,part\n",
                encoding="utf-8",
            )
            original = source.read_bytes()
            with patch.object(lego_workflow, "INVENTORY_IMPORTS", root / "imports"):
                copied = lego_workflow.transfer_reviewed_csv(source)
            with copied.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["Image"] for row in rows], ["a.png", "b.png"])
            self.assertEqual(source.read_bytes(), original)

    def test_missing_review_schema_is_rejected_without_partial_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "results.csv"
            source.write_text("Part ID,Color ID\n3001,5\n")
            destination = root / "imports"
            with patch.object(lego_workflow, "INVENTORY_IMPORTS", destination):
                with self.assertRaises(ValueError):
                    lego_workflow.transfer_reviewed_csv(source)
                with self.assertRaises(ValueError):
                    lego_workflow.count_unapproved_rows(source)
            self.assertEqual(list(destination.iterdir()), [])

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
