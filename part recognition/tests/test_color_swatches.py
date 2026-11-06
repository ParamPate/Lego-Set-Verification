import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bricklink_colors import BRICKLINK_REVIEW_COLORS, BRICKLINK_COLOR_RGB


class ColorList:
    def __init__(self):
        self.items = {}
        self.selected = ()
        self.rebuilds = 0

    def selection(self):
        return self.selected

    def get_children(self):
        return tuple(self.items)

    def delete(self, *items):
        self.items.clear()
        self.selected = ()
        self.rebuilds += 1

    def insert(self, parent, position, **item):
        self.items[item["iid"]] = item

    def selection_set(self, name):
        self.selected = (name,)

    def focus(self, name):
        pass

    def see(self, name):
        pass


class ColorSwatchTests(unittest.TestCase):
    def make_chooser(self):
        path = Path(__file__).resolve().parents[1] / "gallery_interface/gallery_gui.py"
        tree = ast.parse(path.read_text())
        chooser = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "show_color_choices")
        functions = [node for node in chooser.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"refresh_color_list", "use_selected_color"}]
        factory = ast.parse("def make():\n    displayed_options = None\n    return locals()\n")
        factory.body[0].body[1:1] = functions
        environment = {
            "chooser": Mock(winfo_exists=lambda: True), "color_list": ColorList(),
            "color_options_by_image": {"piece.png": {"Blue": "7", "Dark Turquoise": "39", "White": "1"}},
            "image_name": "piece.png", "result": {"LEGO Color": "Dark Turquoise", "BrickLink Color ID": "39"},
            "search_value": Mock(get=Mock(return_value="")), "color_option_requests": set(),
            "color_list_status": Mock(), "swatch_images": {}, "color_review_dropdown": Mock(),
            "color_review_controls": Mock(), "preview_selected_color": Mock(),
            "BRICKLINK_REVIEW_COLORS": BRICKLINK_REVIEW_COLORS, "BRICKLINK_COLOR_RGB": BRICKLINK_COLOR_RGB,
            "Image": Image, "ImageDraw": ImageDraw, "ImageTk": SimpleNamespace(PhotoImage=lambda image, **kwargs: image),
            "tk": SimpleNamespace(END="end"),
        }
        exec(compile(ast.fix_missing_locations(factory), "color chooser", "exec"), environment)
        environment.update(environment["make"]())
        return environment

    def test_swatches_do_not_change_selection_or_rebuild_on_unchanged_poll(self):
        chooser = self.make_chooser()
        chooser["refresh_color_list"]()
        color_list = chooser["color_list"]
        self.assertEqual(color_list.selection(), ("Dark Turquoise",))
        self.assertEqual(len(color_list.items), 3)
        for item in color_list.items.values():
            self.assertEqual(item["image"].size, (28, 18))
        chooser["refresh_color_list"]()
        self.assertEqual(color_list.rebuilds, 1)

    def test_search_and_use_keep_the_original_color_name_and_id(self):
        chooser = self.make_chooser()
        chooser["search_value"].get.return_value = "blue"
        chooser["refresh_color_list"]()
        self.assertEqual(chooser["color_list"].get_children(), ("Blue",))
        chooser["use_selected_color"]()
        chooser["color_review_dropdown"].set.assert_called_once_with("Blue")
        chooser["preview_selected_color"].assert_called_once_with("Blue")
        self.assertEqual(chooser["color_options_by_image"]["piece.png"]["Blue"], "7")


if __name__ == "__main__":
    unittest.main()
