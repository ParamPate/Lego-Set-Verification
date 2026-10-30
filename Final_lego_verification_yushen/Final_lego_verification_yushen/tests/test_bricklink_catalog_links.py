import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import pandas as pd

from src.bricklink_mapping import ASSUMED_MATCH, EXTERNAL_ID_MATCH
from tkinter_app import LegoVerificationApp


class BrickLinkCatalogLinkTests(unittest.TestCase):
    def make_app(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")

        root.withdraw()
        with (
            patch("tkinter_app.list_jobs", return_value=[]),
            patch.object(LegoVerificationApp, "_poll_image_queue"),
            patch.object(root, "after"),
        ):
            app = LegoVerificationApp(root)
        self.addCleanup(root.destroy)
        return app

    @staticmethod
    def relationship_row(bricklink_part_num=""):
        return pd.DataFrame(
            [
                {
                    "rel_type": "M",
                    "related_part_num": "3001pr/1",
                    "bricklink_part_num": bricklink_part_num,
                    "related_part_name": "Test brick",
                    "same_color_available": True,
                    "available_color_names": "Red",
                    "available_color_ids": "4",
                    "related_image_url": "",
                }
            ]
        )

    @staticmethod
    def descendants(widget):
        result = []
        for child in widget.winfo_children():
            result.append(child)
            result.extend(BrickLinkCatalogLinkTests.descendants(child))
        return result

    def test_only_confirmed_mappings_are_attached_to_catalog_rows(self):
        details_df = pd.DataFrame(
            {
                "related_part_num": ["3001pr0001", "3002"],
            }
        )
        mapping_df = pd.DataFrame(
            [
                {
                    "rebrickable_part_num": "3001pr0001",
                    "bricklink_part_num": "3001pb001",
                    "part_name": "Printed brick",
                    "resolved_via": EXTERNAL_ID_MATCH,
                },
                {
                    "rebrickable_part_num": "3002",
                    "bricklink_part_num": "3002",
                    "part_name": "Brick",
                    "resolved_via": ASSUMED_MATCH,
                },
            ]
        )

        with patch(
            "tkinter_app.resolve_rebrickable_part_nums",
            return_value=mapping_df,
        ):
            result_df = (
                LegoVerificationApp
                ._attach_confirmed_bricklink_catalog_mappings(details_df)
            )

        self.assertEqual(
            result_df["bricklink_part_num"].tolist(),
            ["3001pb001", ""],
        )

    def test_confirmed_row_enables_button_and_opens_encoded_catalog_url(self):
        app = self.make_app()

        with patch.object(app, "_load_tree_images"):
            app._render_relationship_details(
                "3001",
                self.relationship_row("3001pr/1"),
            )

        item_id = app.relationship_tree.get_children()[0]
        values = app.relationship_tree.item(item_id, "values")
        self.assertEqual(values[2], "3001pr/1")

        app.relationship_tree.selection_set(item_id)
        app._update_bricklink_catalog_button()
        self.assertEqual(
            str(app.open_bricklink_catalog_button["state"]),
            "normal",
        )

        with patch(
            "tkinter_app.webbrowser.open_new_tab",
            return_value=True,
        ) as open_new_tab:
            app.open_selected_relationship_in_bricklink()

        open_new_tab.assert_called_once_with(
            "https://www.bricklink.com/v2/catalog/catalogitem.page?P="
            "3001pr%2F1"
        )

    def test_unmapped_row_is_visible_but_catalog_button_stays_disabled(self):
        app = self.make_app()

        with patch.object(app, "_load_tree_images"):
            app._render_relationship_details(
                "3001",
                self.relationship_row(),
            )

        item_id = app.relationship_tree.get_children()[0]
        values = app.relationship_tree.item(item_id, "values")
        self.assertEqual(values[2], "Not mapped")

        app.relationship_tree.selection_set(item_id)
        app._update_bricklink_catalog_button()
        self.assertEqual(
            str(app.open_bricklink_catalog_button["state"]),
            "disabled",
        )

        with (
            patch("tkinter_app.messagebox.showwarning") as warning,
            patch("tkinter_app.webbrowser.open_new_tab") as open_new_tab,
        ):
            app.open_selected_relationship_in_bricklink()

        warning.assert_called_once()
        open_new_tab.assert_not_called()

    def test_candidate_review_opens_catalog_and_returns_manual_choice(self):
        app = self.make_app()
        ambiguity_records = [{
            "rebrickable_part_num": "3001pr0001",
            "part_name": "Printed brick",
            "candidates": ["3001pb001", "3001pb/002"],
        }]

        def inspect_and_choose_second_candidate():
            dialog = next(
                child
                for child in app.root.winfo_children()
                if isinstance(child, tk.Toplevel)
            )
            widgets = self.descendants(dialog)
            tree = next(
                widget for widget in widgets if isinstance(widget, ttk.Treeview)
            )
            second_item = next(
                item_id
                for item_id in tree.get_children()
                if tree.item(item_id, "values")[3] == "3001pb/002"
            )
            tree.selection_set(second_item)

            buttons = {
                str(widget.cget("text")): widget
                for widget in widgets
                if isinstance(widget, ttk.Button)
            }
            buttons["Open Selected Candidate in BrickLink"].invoke()
            buttons["Use Selected ID"].invoke()
            buttons["Apply Selections"].invoke()

        app.root.after(25, inspect_and_choose_second_candidate)
        with patch(
            "tkinter_app.webbrowser.open_new_tab",
            return_value=True,
        ) as open_new_tab:
            choices = app._choose_bricklink_part_mappings(ambiguity_records)

        open_new_tab.assert_called_once_with(
            "https://www.bricklink.com/v2/catalog/catalogitem.page?P="
            "3001pb%2F002"
        )
        self.assertEqual(choices, {"3001pr0001": "3001pb/002"})


if __name__ == "__main__":
    unittest.main()
