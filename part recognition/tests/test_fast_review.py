"""Run with: python -m unittest discover -s tests (from part recognition)."""

import ast
import contextlib
import csv
import io
import importlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import main
import review_manager as reviews
from review_policy import apply_review_policy, has_identifiers, needs_attention, review_group


def prediction(**changes):
    result = {
        "Image": "piece.png", "Part ID": "3001", "Part Name": "Brick",
        "Part Confidence": "80%", "Part Review Required": "No",
        "Color Confidence": "80%", "Color Review Required": "No",
        "LEGO Color": "Red", "BrickLink Color ID": "5",
        "Predicted LEGO Color": "Red", "Predicted BrickLink Color ID": "5",
        "Color Method": "Brickognize Color Prediction", "Type": "part",
    }
    result.update(changes)
    return result


def gallery_functions(*names, nested=False, **environment):
    """Exercise real GUI handlers without starting Tk or making web requests."""
    tree = ast.parse((ROOT / "gallery_interface" / "gallery_gui.py").read_text())
    if nested:
        tree = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "build_gallery_interface")
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(body) == len(names)
    namespace = dict(Path=Path, needs_attention=needs_attention, review_group=review_group,
                     has_identifiers=has_identifiers, apply_review_policy=apply_review_policy)
    namespace.update(environment)
    exec(compile(ast.Module(body=body, type_ignores=[]), "gallery handlers", "exec"), namespace)
    return namespace


class ReviewPolicyTests(unittest.TestCase):
    def test_part_and_color_thresholds_are_independent(self):
        for field in ("Part Confidence", "Color Confidence"):
            for score, ready in (("79.99%", False), ("80%", True), ("100%", True),
                                 ("101%", False), ("nan", False), ("", False)):
                with self.subTest(field=field, score=score):
                    result = apply_review_policy(prediction(**{field: score}))
                    self.assertEqual(result["Final Review Status"] == "Ready", ready)

    def test_fallback_requires_review_even_with_high_score(self):
        result = apply_review_policy(prediction(**{
            "Color Method": "Average RGB Fallback", "Color Confidence": "100%",
        }))
        self.assertEqual(result["Final Review Status"], "Needs Color Review")
        self.assertEqual(result["Color Approval Status"], "Pending Review")

    def test_missing_identifiers_cannot_be_approved_even_with_old_manual_flags(self):
        for field in ("Part ID", "BrickLink Color ID"):
            for value in (None, "", "Unknown", "N/A"):
                result = prediction(**{field: value, "Part Review Completed": "Yes",
                                       "Color Review Completed": "Yes"})
                self.assertTrue(needs_attention(result))
                with self.assertRaises(ValueError):
                    reviews.approve_reviewed_result(result)
        self.assertTrue(has_identifiers(prediction(**{"BrickLink Color ID": 0})))
        self.assertTrue(needs_attention(prediction(**{"BrickLink Color ID": "invalid"})))
        self.assertTrue(needs_attention(None))

    def test_manual_corrections_survive_conservative_policy(self):
        result = prediction(**{"Part Review Completed": "Yes", "Color Review Completed": "Yes",
                               "Part Approval Status": "Corrected", "Color Approval Status": "Corrected",
                               "Part Confidence": "20%", "Color Method": "Average RGB Fallback"})
        self.assertEqual(apply_review_policy(result)["Final Review Status"], "Ready")
        self.assertEqual(result["Part Approval Status"], "Corrected")

    def test_backend_applies_policy_to_real_parsing(self):
        response = {"detected_items": [{
            "bounding_boxes": [{"left": 0, "upper": 0, "right": 2, "lower": 2, "score": 1}],
            "candidate_items": [{"id": "part-3001", "score": .8, "type": "part", "name": "Brick"}],
        }]}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            image = Path(directory) / "piece.png"
            main.Image.new("RGB", (2, 2), (196, 40, 27)).save(image)
            with patch.object(main.requests, "post", return_value=Mock(status_code=200, json=lambda: response)), \
                    patch.object(main, "save_api_response"):
                result = main.identify_part(image)
        self.assertEqual(result["Color Method"], "Average RGB Fallback")
        self.assertEqual(result["Final Review Status"], "Needs Color Review")


class ReviewPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.csv_path = Path(temporary.name) / "results.csv"
        self.review_path = Path(temporary.name) / "reviews.json"
        for name, path in (("CSV_FILE", self.csv_path), ("REVIEW_FILE", self.review_path)):
            patcher = patch.object(reviews, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_rows(self, rows):
        with self.csv_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_group_subset_preserves_rows_predictions_and_reload(self):
        rows = [prediction(**{"Image": f"piece{i}.png", "Part Confidence": "60%"}) for i in range(3)]
        self.write_rows(rows)
        for row in (rows[0], rows[2]):
            approved = reviews.approve_reviewed_result(row)
            self.assertTrue(reviews.sync_reviewed_result_to_csv(approved))
            self.assertEqual(reviews.load_part_review(row["Image"])["Part Approval Status"], "Confirmed")
            self.assertEqual(reviews.load_color_review(row["Image"])["Confirmed Color"], "Red")
        with self.csv_path.open() as handle:
            saved = list(csv.DictReader(handle))
        self.assertEqual(len(saved), 3)
        self.assertEqual([row.get("Final Review Status") for row in saved], ["Ready", "", "Ready"])
        self.assertEqual([row["Part Confidence"] for row in saved], ["60%"] * 3)
        self.assertEqual(saved[0]["Part Approval Status"], "Confirmed")

    def test_restored_final_ids_and_manual_corrections_remain_ready(self):
        result = prediction(**{
            "Final Part ID": "3002", "Final LEGO Color": "Blue",
            "Final BrickLink Color ID": "7", "Part Confidence": "20%",
            "Color Confidence": "20%", "Part Review Completed": "Yes",
            "Color Review Completed": "Yes", "Part Approval Status": "Corrected",
        })
        self.write_rows([result])
        environment = gallery_functions("get_recorded_csv_results", RESULTS_FOLDER=self.csv_path.parent,
                                        csv=csv)
        # The production loader uses the established filename.
        self.csv_path.rename(self.csv_path.parent / "brickognize_results.csv")
        restored = environment["get_recorded_csv_results"]()["piece.png"]
        self.assertEqual(restored["Part ID"], "3002")
        self.assertEqual(review_group(restored), ("3002", "7"))
        self.assertFalse(needs_attention(restored))
        self.assertEqual(restored["Part Approval Status"], "Corrected")

    def test_inventory_counts_one_per_eligible_image_after_review(self):
        import pandas as pd
        # Use the actual inventory importer with catalog calls mocked; no network/cache writes.
        inventory_root = ROOT.parent / "Final_lego_verification_yushen" / "Final_lego_verification_yushen"
        sys.path.insert(0, str(inventory_root))
        with patch("requests_cache.CachedSession"):
            importer = importlib.import_module("src.user_input")
        rows = [prediction(**{"Image": f"piece{i}.png", "Part Confidence": "60%"}) for i in range(3)]
        self.write_rows(rows)
        for row in rows[:2]:
            self.assertTrue(reviews.sync_reviewed_result_to_csv(reviews.approve_reviewed_result(row)))
        self.assertTrue(reviews.sync_reviewed_result_to_csv(rows[2]))
        color_map = pd.DataFrame([{"bricklink_color_id": 5, "rebrickable_color_id": 4,
                                   "bricklink_color_name": "Red", "rebrickable_color_name": "Red"}])
        part_map = pd.DataFrame([{"bricklink_part_num": "3001", "rebrickable_part_num": "3001",
                                  "resolved_via": "direct"}])
        with patch.object(importer, "load_color_map", return_value=color_map), \
                patch.object(importer, "resolve_bricklink_part_nums", return_value=part_map):
            have, summary = importer.load_from_csv(self.csv_path)
        self.assertEqual(summary.counted_rows, 2)
        self.assertEqual(summary.skipped_not_ready, 1)
        self.assertEqual(int(have["quantity_have"].sum()), 2)

    def test_corrected_history_is_not_overwritten_by_group_approval(self):
        result = prediction(**{"Part Review Completed": "Yes", "Part Approval Status": "Corrected"})
        with patch.object(reviews, "save_part_review") as save_part:
            approved = reviews.approve_reviewed_result(result)
        save_part.assert_not_called()
        self.assertEqual(approved["Part Approval Status"], "Corrected")

    def test_approval_uses_corrected_color_in_history_and_reference(self):
        result = prediction(**{"Confirmed LEGO Color": "Blue", "Confirmed BrickLink Color ID": "7",
                               "BrickLink URL": "https://example.test/part?P=3001&idColor=5"})
        approved = reviews.approve_reviewed_result(result)
        self.assertEqual(approved["LEGO Color"], "Blue")
        self.assertEqual(approved["Color Approval Status"], "Corrected")
        self.assertTrue(approved["Reference Image URL"].endswith("/7/3001.png"))
        self.assertTrue(approved["BrickLink URL"].endswith("&idColor=7"))
        self.assertEqual(result["Predicted LEGO Color"], "Red")

    def test_csv_replace_failure_keeps_previous_csv(self):
        result = prediction()
        self.write_rows([result])
        before = self.csv_path.read_bytes()
        with patch.object(Path, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                reviews.sync_reviewed_result_to_csv(result)
        self.assertEqual(self.csv_path.read_bytes(), before)

    def test_history_replace_failure_keeps_previous_history(self):
        self.review_path.write_text('{"previous": {}}')
        with patch.object(Path, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                reviews.approve_reviewed_result(prediction())
        self.assertEqual(self.review_path.read_text(), '{"previous": {}}')

    def test_corrupt_history_is_not_overwritten_by_approval(self):
        self.review_path.write_text("incomplete JSON")
        with self.assertRaises(OSError):
            reviews.approve_reviewed_result(prediction())
        self.assertEqual(self.review_path.read_text(), "incomplete JSON")


class GalleryHandlerTests(unittest.TestCase):
    def test_opening_outputs_uses_the_platform_application(self):
        for platform in ("darwin", "win32"):
            with self.subTest(platform=platform):
                os_module = SimpleNamespace(startfile=Mock())
                subprocess = Mock()
                environment = gallery_functions("open_local_path", sys=SimpleNamespace(platform=platform),
                                                os=os_module, subprocess=subprocess)
                environment["open_local_path"](Path("output.csv"))
                if platform == "darwin":
                    subprocess.Popen.assert_called_once_with(["open", "output.csv"])
                    os_module.startfile.assert_not_called()
                else:
                    os_module.startfile.assert_called_once_with(Path("output.csv"))
                    subprocess.Popen.assert_not_called()

    def test_attention_filter_includes_failed_and_missing_ids_and_preserves_selection(self):
        paths = ["ready.png", "uncertain.png", "failed.png", "incomplete.png"]
        results = {"ready.png": prediction(), "uncertain.png": prediction(**{"Part Confidence": "60%"}),
                   "incomplete.png": prediction(**{"BrickLink Color ID": ""})}
        selected = [1]
        drawn = []
        listbox = Mock()
        listbox.curselection.side_effect = lambda: tuple(selected)
        listbox.delete.side_effect = lambda *args: (drawn.clear(), selected.clear())
        listbox.size.side_effect = lambda: len(drawn)
        listbox.selection_set.side_effect = lambda index: selected.append(index)
        environment = gallery_functions(
            "apply_image_filter", image_filter_dropdown=Mock(get=lambda: "Needs attention"),
            visible_image_paths=list(paths), selected_image_paths=paths, processed_results=results,
            image_listbox=listbox, tk=SimpleNamespace(END="end"),
            process_button=Mock(cget=lambda key: "normal"),
            set_image_list_row=Mock(side_effect=lambda index, path: drawn.append(path)), show_selected_image=Mock(),
            processing_status_label=Mock(), color_save_status_label=Mock(),
        )
        environment["apply_image_filter"]()
        self.assertEqual(set(drawn), {"uncertain.png", "failed.png", "incomplete.png"})
        self.assertEqual(drawn[selected[0]], "uncertain.png")

    def test_group_approval_excludes_unselected_piece(self):
        rows = {f"piece{i}.png": prediction(**{"Image": f"piece{i}.png"}) for i in range(3)}
        approve = Mock(side_effect=lambda result: dict(result, approved=True))
        environment = gallery_functions(
            "approve_selected", nested=True, selected_for_approval={"piece0.png", "piece2.png"},
            visible_image_paths=list(rows), processed_results=rows, approve_reviewed_result=approve,
            process_button=Mock(cget=lambda key: "normal"), queue_csv_review=Mock(),
            update_batch_summary=Mock(), apply_image_filter=Mock(), rebuild_gallery=Mock(),
            messagebox=Mock(), window=Mock(),
        )
        environment["approve_selected"]()
        self.assertTrue(rows["piece0.png"]["approved"])
        self.assertNotIn("approved", rows["piece1.png"])
        self.assertTrue(rows["piece2.png"]["approved"])

    def test_enter_does_not_approve_a_different_identity_after_filtering(self):
        approve = Mock()
        environment = gallery_functions(
            "gallery_action", nested=True, selected_index=lambda: 0,
            visible_image_paths=["new.png"], state={"rendered_path": "old.png"},
            approve_current_and_next=approve, rebuild_gallery=Mock(), window=Mock(), update_details=Mock(),
        )
        environment["gallery_action"](approve)
        approve.assert_not_called()
        environment["state"]["rendered_path"] = "new.png"
        environment["gallery_action"](approve)
        approve.assert_called_once()

    def test_shortcuts_leave_text_and_other_windows_alone(self):
        window = Mock()
        focus = Mock(winfo_toplevel=lambda: window, winfo_class=lambda: "TEntry")
        window.focus_get.return_value = focus
        environment = gallery_functions(
            "review_shortcut", nested=True, window=window, gallery_action=Mock(),
            process_button=Mock(cget=lambda key: "normal"), approve_current_and_next=Mock(),
        )
        environment["review_shortcut"](SimpleNamespace(keysym="Return"))
        environment["gallery_action"].assert_not_called()
        focus.winfo_class = lambda: "Canvas"
        self.assertEqual(environment["review_shortcut"](SimpleNamespace(keysym="Return")), "break")
        environment["gallery_action"].assert_called_once()

    def test_failed_status_save_stays_pending(self):
        environment = gallery_functions(
            "update_all_approval_statuses", pending_csv_reviews={"piece.png": prediction()},
            sync_reviewed_result_to_csv=Mock(return_value=False), update_all_statuses_button=Mock(),
            processing_status_label=Mock(), show_save_confirmation=Mock(),
        )
        self.assertFalse(environment["update_all_approval_statuses"]())
        self.assertIn("piece.png", environment["pending_csv_reviews"])
        environment["show_save_confirmation"].assert_not_called()

    def test_reference_prefetch_reuses_inflight_and_cached_requests(self):
        thread = Mock()
        environment = gallery_functions(
            "request_reference_image", reference_image_cache={}, reference_image_loading=set(),
            reference_image_failures=set(), threading=Mock(Thread=thread),
        )
        environment["request_reference_image"]("https://example.test/part.png")
        environment["request_reference_image"]("https://example.test/part.png")
        thread.assert_called_once()
        environment["reference_image_cache"]["https://example.test/cached.png"] = object()
        environment["request_reference_image"]("https://example.test/cached.png")
        thread.assert_called_once()

    def test_failed_save_prevents_batch_completion(self):
        reset = Mock()
        environment = gallery_functions(
            "complete_current_batch", selected_image_paths=["piece.png"], processed_results={"piece.png": prediction()},
            pending_csv_reviews={"piece.png": prediction()}, process_button=Mock(cget=lambda key: "normal"),
            rescan_part_button=Mock(cget=lambda key: "Rescan Part"),
            color_save_status_label=Mock(cget=lambda key: "Saved"), queue_csv_review=Mock(),
            update_all_approval_statuses=Mock(return_value=False), _reset_current_batch_workspace=reset,
            messagebox=Mock(askyesno=Mock(return_value=True)), window=Mock(),
        )
        environment["complete_current_batch"]()
        reset.assert_not_called()


if __name__ == "__main__":
    unittest.main()
