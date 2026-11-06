from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_fast_review import gallery_functions
from folder_batch import folder_images


class FolderDiscoveryTests(unittest.TestCase):
    def test_nested_images_and_generated_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("images/one.PNG", "images01/two.jpg", "analysis/overview.png",
                         "analysis01/mask.png", "recognition_old/captured.png", "results/old.jpg"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertEqual([path.name for path in folder_images(root)], ["one.PNG", "two.jpg"])

    def test_duplicate_filenames_are_rejected_instead_of_reusing_another_piece(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("a/piece.JPG", "b/PIECE.jpg"):
                path = root / name
                path.parent.mkdir()
                path.touch()
            with self.assertRaisesRegex(ValueError, "Rename repeated"):
                folder_images(root)

    def test_empty_folder_and_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "No JPG"):
                folder_images(root)
            image = root / "piece.png"
            image.touch()
            with self.assertRaisesRegex(ValueError, "Drop one folder"):
                folder_images(image)


class FolderDropTests(unittest.TestCase):
    def environment(self, root):
        pointer = root / "last_batch.txt"
        backend = SimpleNamespace(RESULTS_FILE=root / "previous.csv", API_RESPONSES_FILE=root / "previous.json")
        reviews = SimpleNamespace(CSV_FILE=backend.RESULTS_FILE, REVIEW_FILE=root / "previous_reviews.json")
        return gallery_functions(
            "record_active_batch", "analyze_folder", folder_images=folder_images, tempfile=tempfile, LAST_BATCH_FILE=pointer,
            recognition_backend=backend, review_backend=reviews,
            process_button=Mock(cget=lambda key: "normal"), rescan_part_button=Mock(cget=lambda key: "Rescan Part"),
            color_save_status_label=Mock(cget=lambda key: "Saved"), pending_csv_reviews={},
            update_all_approval_statuses=Mock(return_value=True), _reset_current_batch_workspace=Mock(),
            upload_images=Mock(), start_batch_processing=Mock(), processing_status_label=Mock(),
        )

    def test_drop_starts_automatically_with_separate_results_and_correct_backend_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "folder with spaces"
            source.mkdir()
            (source / "piece.png").touch()
            (root / "previous.csv").write_text("keep this batch")
            environment = self.environment(root)
            self.assertTrue(environment["analyze_folder"](source))
            first = environment["RESULTS_FOLDER"]
            environment["upload_images"].assert_called_once_with([str((source / "piece.png").resolve())])
            environment["start_batch_processing"].assert_called_once()
            self.assertEqual(environment["review_backend"].CSV_FILE, first / "brickognize_results.csv")
            self.assertEqual(environment["recognition_backend"].API_RESPONSES_FILE, first / "brickognize_responses.json")
            self.assertEqual((root / "last_batch.txt").read_text(), str(first / "brickognize_results.csv"))
            self.assertTrue(environment["analyze_folder"](source))
            self.assertNotEqual(environment["RESULTS_FOLDER"], first)
            self.assertEqual((root / "previous.csv").read_text(), "keep this batch")

    def test_busy_batch_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = self.environment(root)
            environment["process_button"].cget = lambda key: "disabled"
            self.assertFalse(environment["analyze_folder"](root))
            environment["_reset_current_batch_workspace"].assert_not_called()
            environment["start_batch_processing"].assert_not_called()

    def test_failed_pending_save_blocks_switching_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "piece.png").touch()
            environment = self.environment(root)
            environment["pending_csv_reviews"] = {"old.png": {}}
            environment["update_all_approval_statuses"].return_value = False
            self.assertFalse(environment["analyze_folder"](root))
            environment["_reset_current_batch_workspace"].assert_not_called()
            self.assertFalse(list(root.glob("recognition_*")))

    def test_drop_uses_tcl_list_parser_for_spaced_folder_names(self):
        window = SimpleNamespace(tk=Mock(splitlist=Mock(return_value=("/a folder/images",))))
        analyze = Mock(return_value=True)
        environment = gallery_functions("on_folder_drop", window=window, analyze_folder=analyze,
                                        processing_status_label=Mock(), tk=SimpleNamespace(TclError=RuntimeError))
        self.assertEqual(environment["on_folder_drop"](SimpleNamespace(data="{/a folder/images}")), "copy")
        window.tk.splitlist.assert_called_once_with("{/a folder/images}")
        analyze.assert_called_once_with("/a folder/images")

    def test_multiple_drops_are_rejected(self):
        analyze = Mock()
        environment = gallery_functions(
            "on_folder_drop", window=SimpleNamespace(tk=Mock(splitlist=Mock(return_value=("/a", "/b")))),
            analyze_folder=analyze, processing_status_label=Mock(), tk=SimpleNamespace(TclError=RuntimeError),
        )
        self.assertEqual(environment["on_folder_drop"](SimpleNamespace(data="/a /b")), "refuse_drop")
        analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
