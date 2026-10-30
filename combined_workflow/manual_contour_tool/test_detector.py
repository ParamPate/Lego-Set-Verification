import tempfile
import unittest
from pathlib import Path

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


class OutputFolderTests(unittest.TestCase):
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
