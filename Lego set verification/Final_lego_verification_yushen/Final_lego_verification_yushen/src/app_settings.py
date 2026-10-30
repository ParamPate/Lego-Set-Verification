"""Persist small, machine-local application preferences."""

import json
import os
from pathlib import Path
import tempfile

from config import app_settings_path


AUTOMATIC_DATA_UPDATE_DAYS = 7
DEFAULT_APP_SETTINGS = {
    "automatic_data_updates": True,
}


def load_app_settings(path=app_settings_path):
    settings = DEFAULT_APP_SETTINGS.copy()
    settings_path = Path(path)
    if not settings_path.exists():
        return settings

    try:
        with open(settings_path, encoding="utf-8") as settings_file:
            saved_settings = json.load(settings_file)
    except (OSError, ValueError, UnicodeDecodeError):
        return settings

    if isinstance(saved_settings, dict):
        automatic = saved_settings.get("automatic_data_updates")
        if isinstance(automatic, bool):
            settings["automatic_data_updates"] = automatic

    return settings


def save_app_settings(settings, path=app_settings_path):
    settings_path = Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "automatic_data_updates": bool(
            settings.get("automatic_data_updates", True)
        ),
    }

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".tmp",
            dir=settings_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            json.dump(payload, temporary_file, indent=2)

        os.replace(temporary_path, settings_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    return payload
