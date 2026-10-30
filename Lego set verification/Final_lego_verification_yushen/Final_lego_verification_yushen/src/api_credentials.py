"""Load and safely persist the user's Rebrickable API key."""

import os
from pathlib import Path
import tempfile

from dotenv import dotenv_values

from config import env_path


API_KEY_NAME = "rebrickable_api_key"
ALTERNATE_API_KEY_NAME = "REBRICKABLE_API_KEY"
SOURCE_USER_FILE = "user_file"
SOURCE_ENVIRONMENT = "environment"
SOURCE_LEGACY_FILE = "legacy_file"
SOURCE_MISSING = "missing"


def normalize_api_key(value):
    """Return a trimmed one-line API key suitable for storage and headers."""
    raw_value = str(value or "")
    if "\n" in raw_value or "\r" in raw_value:
        raise ValueError("The API key must be a single line.")
    return raw_value.strip()


def _key_from_file(path):
    values = dotenv_values(path, interpolate=False)
    for name in (API_KEY_NAME, ALTERNATE_API_KEY_NAME):
        if name in values:
            return normalize_api_key(values.get(name)), True
    return "", False


def load_api_key(
    managed_path=env_path,
    environ=None,
):
    """Return ``(key, source)`` without placing the secret in ``os.environ``.

    Once the app-managed file exists it is authoritative, including when its
    value is blank after the user removes a key. Before that, environment and
    legacy project values keep development checkouts backward compatible.
    """
    managed_path = Path(managed_path)
    if managed_path.exists():
        try:
            key, _present = _key_from_file(managed_path)
        except (OSError, UnicodeError, ValueError):
            key = ""
        return key, SOURCE_USER_FILE

    environment = os.environ if environ is None else environ
    for name in (ALTERNATE_API_KEY_NAME, API_KEY_NAME):
        if name in environment:
            key = normalize_api_key(environment.get(name))
            if key:
                return key, SOURCE_ENVIRONMENT

    return "", SOURCE_MISSING


def save_api_key(api_key, path=env_path):
    """Atomically store the key in the app-managed per-user ``.env`` file."""
    key = normalize_api_key(api_key)
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    escaped_key = key.replace("\\", "\\\\").replace('"', '\\"')
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".tmp",
            dir=target_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            temporary_file.write(f'{API_KEY_NAME}="{escaped_key}"\n')

        os.replace(temporary_path, target_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    return target_path


def clear_api_key(path=env_path):
    """Remove the stored secret while keeping an intentional blank override."""
    return save_api_key("", path=path)
