import unittest

import pandas as pd

from tkinter_app import LegoVerificationApp


class StatusValue:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class StateButton:
    def __init__(self):
        self.state = None

    def configure(self, **kwargs):
        self.state = kwargs.get("state", self.state)


class ExplodingTree:
    def identify_row(self, _coordinate):
        raise AssertionError("busy guard did not stop the edit handler")


class BusyEditGuardTests(unittest.TestCase):
    def test_review_button_is_disabled_while_background_work_is_busy(self):
        app = LegoVerificationApp.__new__(LegoVerificationApp)
        app.variation_proposals = pd.DataFrame([{"proposal": 1}])
        app.workflow_stage = app.parts_entry_stage
        app.review_variations_button = StateButton()

        app.busy = True
        app._update_review_variations_button()
        self.assertEqual(app.review_variations_button.state, "disabled")

        app.busy = False
        app._update_review_variations_button()
        self.assertEqual(app.review_variations_button.state, "normal")

    def test_have_edit_is_blocked_with_visible_status_explanation(self):
        app = LegoVerificationApp.__new__(LegoVerificationApp)
        app.busy = True
        app.busy_status = "Importing batch-2.csv..."
        app.status_var = StatusValue()
        app.parts_df = pd.DataFrame([{"part_num": "3001"}])
        app.parts_tree = ExplodingTree()

        result = app._begin_quantity_edit(type("Event", (), {"x": 1, "y": 1})())

        self.assertEqual(result, "break")
        self.assertEqual(
            app.status_var.value,
            "Importing batch-2.csv... Editing Have quantities is temporarily "
            "disabled.",
        )

    def test_variation_review_is_blocked_with_visible_status_explanation(self):
        app = LegoVerificationApp.__new__(LegoVerificationApp)
        app.busy = True
        app.busy_status = "Comparing parts..."
        app.status_var = StatusValue()

        result = app.review_variation_choices()

        self.assertEqual(result, "break")
        self.assertEqual(
            app.status_var.value,
            "Comparing parts... Variation review is temporarily disabled.",
        )


if __name__ == "__main__":
    unittest.main()
