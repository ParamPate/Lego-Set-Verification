from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import requests

from src.api_credentials import (
    SOURCE_ENVIRONMENT,
    SOURCE_LEGACY_FILE,
    SOURCE_USER_FILE,
    clear_api_key,
    load_api_key,
    save_api_key,
)
from src import api_rebrickable
from tkinter_app import LegoVerificationApp


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}"
            )


class ApiCredentialTests(unittest.TestCase):
    @patch("src.api_rebrickable.session.get")
    def test_regular_api_requests_allow_a_sixty_second_response(self, get):
        get.return_value = FakeResponse(200)

        response = api_rebrickable._request(
            "https://example.test/large-set",
            params={"page_size": 1000},
            headers={"Authorization": "key hidden"},
        )

        self.assertIs(response, get.return_value)
        self.assertEqual(
            get.call_args.kwargs["timeout"],
            (10, 60),
        )

    def test_multiline_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "single line"):
                save_api_key(
                    "first-line\nsecond-line",
                    path=Path(temp_dir) / ".env",
                )

    def test_managed_key_round_trip_uses_per_user_env_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            managed_path = Path(temp_dir) / "account" / ".env"
            legacy_path = Path(temp_dir) / "project.env"
            legacy_path.write_text(
                "rebrickable_api_key=legacy-key\n",
                encoding="utf-8",
            )

            save_api_key(' new-key-"quoted" ', path=managed_path)
            key, source = load_api_key(
                managed_path=managed_path,
                legacy_path=legacy_path,
                environ={},
            )

            self.assertEqual(key, 'new-key-"quoted"')
            self.assertEqual(source, SOURCE_USER_FILE)
            self.assertNotIn("legacy-key", managed_path.read_text("utf-8"))

    def test_blank_managed_key_intentionally_shadows_legacy_and_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            managed_path = Path(temp_dir) / "managed.env"
            legacy_path = Path(temp_dir) / "legacy.env"
            legacy_path.write_text(
                "rebrickable_api_key=legacy-key\n",
                encoding="utf-8",
            )
            clear_api_key(path=managed_path)

            key, source = load_api_key(
                managed_path=managed_path,
                legacy_path=legacy_path,
                environ={"REBRICKABLE_API_KEY": "environment-key"},
            )

            self.assertEqual(key, "")
            self.assertEqual(source, SOURCE_USER_FILE)

    def test_environment_then_legacy_are_backward_compatible_fallbacks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            managed_path = Path(temp_dir) / "missing-managed.env"
            legacy_path = Path(temp_dir) / "legacy.env"
            legacy_path.write_text(
                "rebrickable_api_key=legacy-key\n",
                encoding="utf-8",
            )

            key, source = load_api_key(
                managed_path=managed_path,
                legacy_path=legacy_path,
                environ={"REBRICKABLE_API_KEY": "environment-key"},
            )
            self.assertEqual(
                (key, source),
                ("environment-key", SOURCE_ENVIRONMENT),
            )

            key, source = load_api_key(
                managed_path=managed_path,
                legacy_path=legacy_path,
                environ={},
            )
            self.assertEqual((key, source), ("legacy-key", SOURCE_LEGACY_FILE))

    @patch("src.api_rebrickable.requests.get")
    def test_validation_uses_direct_uncached_request(self, get):
        get.return_value = FakeResponse(200)

        self.assertTrue(
            api_rebrickable.validate_rebrickable_api_key("candidate-key")
        )

        get.assert_called_once()
        call = get.call_args
        self.assertEqual(
            call.kwargs["headers"]["Authorization"],
            "key candidate-key",
        )
        self.assertEqual(call.kwargs["params"], {"page_size": 1})

    @patch("src.api_rebrickable.requests.get")
    def test_validation_rejects_unauthorized_key_without_activating_it(self, get):
        get.return_value = FakeResponse(401)
        previous_key = api_rebrickable.rebrickable_api_key
        previous_source = api_rebrickable.rebrickable_api_key_source
        self.addCleanup(
            api_rebrickable.configure_rebrickable_api_key,
            previous_key,
            previous_source,
        )

        with self.assertRaisesRegex(
            api_rebrickable.RebrickableAPIError,
            "API key rejected",
        ):
            api_rebrickable.validate_rebrickable_api_key("bad-key")

        self.assertEqual(api_rebrickable.rebrickable_api_key, previous_key)

    def test_runtime_key_can_be_replaced_without_restart(self):
        previous_key = api_rebrickable.rebrickable_api_key
        previous_source = api_rebrickable.rebrickable_api_key_source
        self.addCleanup(
            api_rebrickable.configure_rebrickable_api_key,
            previous_key,
            previous_source,
        )

        api_rebrickable.configure_rebrickable_api_key(
            "replacement-key",
            source=SOURCE_USER_FILE,
        )

        self.assertEqual(
            api_rebrickable._authorization_headers(),
            {"Authorization": "key replacement-key"},
        )
        self.assertEqual(
            api_rebrickable.get_rebrickable_api_key_status(),
            {"configured": True, "source": SOURCE_USER_FILE},
        )


class ApiKeyDialogTests(unittest.TestCase):
    def make_app(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")

        root.withdraw()
        with (
            patch.object(LegoVerificationApp, "_poll_image_queue"),
            patch.object(root, "after"),
        ):
            app = LegoVerificationApp(root)
        self.addCleanup(root.destroy)
        return root, app

    @patch("tkinter_app.get_rebrickable_api_key_status")
    def test_settings_menu_opens_masked_key_manager(self, status):
        status.return_value = {
            "configured": False,
            "source": "missing",
        }
        _root, app = self.make_app()

        self.assertEqual(
            app.settings_menu.entrycget(0, "label"),
            "Rebrickable API Key...",
        )
        app.open_api_key_settings()

        self.assertEqual(str(app.api_key_entry["show"]), "*")
        self.assertEqual(app.api_key_entry_var.get(), "")
        self.assertIn("No API key", app.api_key_status_var.get())
        self.assertEqual(
            str(app.remove_api_key_button["state"]),
            "disabled",
        )

    @patch("tkinter_app.get_rebrickable_api_key_status")
    @patch("tkinter_app.messagebox.showinfo")
    @patch("tkinter_app.configure_rebrickable_api_key")
    @patch("tkinter_app.save_api_key")
    def test_successful_validation_saves_and_activates_key(
        self,
        save,
        configure,
        showinfo,
        status,
    ):
        status.return_value = {
            "configured": True,
            "source": SOURCE_USER_FILE,
        }
        _root, app = self.make_app()
        app.open_api_key_settings()
        app.api_key_entry_var.set("new-key")
        app.api_key_validation_in_progress = True

        with patch.object(app.root, "after"):
            app._finish_api_key_validation("new-key", None)

        save.assert_called_once_with("new-key")
        configure.assert_called_once_with("new-key", source=SOURCE_USER_FILE)
        showinfo.assert_called_once()
        self.assertEqual(app.api_key_entry_var.get(), "")
        self.assertEqual(app.status_var.get(), "Rebrickable API key updated.")

    @patch("tkinter_app.get_rebrickable_api_key_status")
    def test_first_run_prompt_requires_visible_root_and_missing_key(self, status):
        status.return_value = {
            "configured": False,
            "source": "missing",
        }
        root, app = self.make_app()
        with patch.object(app, "open_api_key_settings") as open_dialog:
            app._prompt_for_api_key_if_missing()
            open_dialog.assert_not_called()

            root.deiconify()
            root.update_idletasks()
            app._prompt_for_api_key_if_missing()

        open_dialog.assert_called_once_with(first_run=True)


if __name__ == "__main__":
    unittest.main()
