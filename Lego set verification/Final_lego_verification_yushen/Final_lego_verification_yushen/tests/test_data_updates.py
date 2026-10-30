import gzip
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import pandas as pd

from src.app_settings import load_app_settings, save_app_settings
from src.bricklink_mapping import refresh_color_map
from src.relationships import refresh_relationships_csv
from tkinter_app import LegoVerificationApp


def relationship_csv(row_count):
    lines = ["rel_type,child_part_num,parent_part_num"]
    lines.extend(
        f"M,child{position},parent{position}"
        for position in range(row_count)
    )
    return "\n".join(lines) + "\n"


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


class DataUpdateTests(unittest.TestCase):
    def test_settings_menu_and_active_job_safeguard(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")

        root.withdraw()
        try:
            app = LegoVerificationApp(root)
            self.assertTrue(str(root["menu"]))

            app.open_data_updates()
            root.update_idletasks()
            self.assertEqual(
                str(app.refresh_relationships_button["state"]),
                "normal",
            )

            app.parts_df = pd.DataFrame([{"part_num": "3001"}])
            app._refresh_data_update_dialog()
            self.assertEqual(
                str(app.refresh_relationships_button["state"]),
                "disabled",
            )
            self.assertIn(
                "cannot be replaced while a set is loaded",
                app.data_update_availability_var.get(),
            )
        finally:
            root.destroy()

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            save_app_settings(
                {"automatic_data_updates": False},
                settings_path,
            )
            loaded = load_app_settings(settings_path)

        self.assertFalse(loaded["automatic_data_updates"])

    def test_invalid_settings_use_safe_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text("not json", encoding="utf-8")
            loaded = load_app_settings(settings_path)

        self.assertTrue(loaded["automatic_data_updates"])

    @patch("src.relationships.requests.get")
    def test_relationship_refresh_validates_backs_up_and_replaces(self, get):
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "part_relationships.csv"
            original = relationship_csv(1200)
            target_path.write_text(original, encoding="utf-8")
            get.return_value = FakeResponse(
                gzip.compress(relationship_csv(1300).encode("utf-8"))
            )

            refreshed_df = refresh_relationships_csv(
                csv_path=target_path,
                download_url="https://example.test/relationships.csv.gz",
            )

            self.assertEqual(len(refreshed_df), 1300)
            self.assertEqual(
                target_path.with_suffix(".csv.bak").read_text(
                    encoding="utf-8"
                ),
                original,
            )
            self.assertEqual(len(pd.read_csv(target_path)), 1300)

    @patch("src.relationships.requests.get")
    def test_invalid_relationship_download_preserves_current_file(self, get):
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "part_relationships.csv"
            original = relationship_csv(1200)
            target_path.write_text(original, encoding="utf-8")
            get.return_value = FakeResponse(
                gzip.compress(b"wrong,column\nvalue,value\n")
            )

            with self.assertRaisesRegex(ValueError, "unexpected columns"):
                refresh_relationships_csv(
                    csv_path=target_path,
                    download_url=(
                        "https://example.test/relationships.csv.gz"
                    ),
                )

            self.assertEqual(
                target_path.read_text(encoding="utf-8"),
                original,
            )
            self.assertFalse(target_path.with_suffix(".csv.bak").exists())

    @patch("src.bricklink_mapping.get_colors_df")
    def test_color_refresh_backs_up_and_replaces(self, get_colors):
        color_rows = pd.DataFrame([{
            "bricklink_color_id": 5,
            "rebrickable_color_id": 4,
            "bricklink_color_name": "Red",
            "rebrickable_color_name": "Red",
        }])
        get_colors.return_value = color_rows

        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "color_map.csv"
            original = "old,color\n1,2\n"
            target_path.write_text(original, encoding="utf-8")

            with patch(
                "src.bricklink_mapping.color_map_csv_path",
                target_path,
            ):
                refreshed_df = refresh_color_map()

            self.assertEqual(len(refreshed_df), 1)
            self.assertEqual(
                target_path.with_suffix(".csv.bak").read_text(
                    encoding="utf-8"
                ),
                original,
            )
            self.assertEqual(
                list(pd.read_csv(target_path).columns),
                list(color_rows.columns),
            )

    def test_reference_file_staleness_uses_weekly_interval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "reference.csv"
            data_path.write_text("value\n1\n", encoding="utf-8")
            now = time.time()

            os.utime(data_path, (now - 6 * 24 * 60 * 60,) * 2)
            self.assertFalse(
                LegoVerificationApp._reference_data_is_stale(
                    data_path,
                    now=now,
                )
            )

            os.utime(data_path, (now - 8 * 24 * 60 * 60,) * 2)
            self.assertTrue(
                LegoVerificationApp._reference_data_is_stale(
                    data_path,
                    now=now,
                )
            )

    @patch("tkinter_app.refresh_color_map")
    @patch("tkinter_app.refresh_relationships_csv")
    def test_combined_refresh_keeps_partial_success(
        self,
        refresh_relationships,
        refresh_colors,
    ):
        refresh_relationships.return_value = pd.DataFrame([{
            "rel_type": "M",
            "child_part_num": "child",
            "parent_part_num": "parent",
        }])
        refresh_colors.side_effect = ValueError("color service unavailable")

        result = LegoVerificationApp._refresh_reference_data(
            ("relationships", "colors")
        )

        self.assertEqual(len(result["relationships_df"]), 1)
        self.assertIsNone(result["color_map_df"])
        self.assertIn("color service unavailable", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
