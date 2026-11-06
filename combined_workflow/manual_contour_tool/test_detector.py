import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import csv

import cv2
import numpy as np

from detector import (
    Settings,
    build_mask,
    crop_filename,
    find_detections,
    next_images_folder,
    next_run_folders,
)
import app as contour_app


class OutputFolderTests(unittest.TestCase):
    def test_automatic_batch_saves_all_photos_and_continues_after_bad_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = np.full((600, 800, 3), 240, dtype=np.uint8)
            cv2.rectangle(source, (100, 100), (300, 300), (20, 20, 20), -1)
            paths = [root / "first.png", root / "broken.png", root / "last.png"]
            cv2.imwrite(str(paths[0]), source)
            paths[1].write_bytes(b"not an image")
            cv2.imwrite(str(paths[2]), source)
            originals = [path.read_bytes() for path in paths]
            callbacks = []
            tool = SimpleNamespace(
                batch_running=False, image_queue=paths, settings=lambda: Settings(),
                output_parent=Mock(get=lambda: str(root / "outputs")), status=Mock(),
                batch_button=Mock(), after=lambda delay, callback: callbacks.append(callback),
                image_path=Mock(), selected=set(), completed_images=set(),
                queue_selector=Mock(), _queue_labels=lambda: [], queue_position=Mock(),
                refresh_preview=Mock(),
            )
            handoff = root / "last_output.txt"
            with patch.object(contour_app, "LAST_OUTPUT_FILE", handoff), \
                    patch.object(contour_app.messagebox, "showerror") as showerror:
                contour_app.ContourTool.analyze_all_photos(tool)
                while callbacks:
                    callbacks.pop(0)()
                showerror.assert_not_called()
            self.assertFalse(tool.batch_running)
            report = next((root / "outputs").glob("batch_*/batch_results.csv"))
            with report.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertTrue(rows[1]["error"])
            self.assertEqual([rows[i]["pieces"] for i in (0, 2)], ["1", "1"])
            self.assertEqual(len(list(Path(handoff.read_text()).rglob("*.png"))), 2)
            self.assertEqual([path.read_bytes() for path in paths], originals)
            self.assertEqual(tool.completed_images, {paths[0], paths[2]})

    def test_numbered_folders_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            self.assertEqual(next_images_folder(parent).name, "images")
            (parent / "images").mkdir()
            self.assertEqual(next_images_folder(parent).name, "images01")
            (parent / "images01").mkdir()
            self.assertEqual(next_images_folder(parent).name, "images02")

    def test_analysis_folder_uses_matching_suffix(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            (parent / "images").mkdir()
            images, analysis = next_run_folders(parent)
            self.assertEqual(images.name, "images01")
            self.assertEqual(analysis.name, "analysis01")

    def test_crop_names_include_the_run_folder_identifier(self):
        self.assertEqual(crop_filename(Path("images"), 1), "image_001.png")
        self.assertEqual(crop_filename(Path("images01"), 7), "image01_007.png")

    def test_auto_mode_separates_parts_on_white_background(self):
        image = np.full((1200, 1600, 3), 240, dtype=np.uint8)
        cv2.rectangle(image, (250, 250), (500, 500), (20, 20, 20), -1)
        cv2.rectangle(image, (900, 500), (1200, 750), (0, 160, 230), -1)
        settings = Settings(minimum_area=500)
        detections = find_detections(build_mask(image, settings), settings)
        self.assertEqual(len(detections), 2)


if __name__ == "__main__":
    unittest.main()
