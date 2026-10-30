"""
Shared path configuration. Every other module imports paths from here
instead of re-deriving them.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

APP_DATA_FOLDER = Path("SK Brickrescue") / "LEGO Set Verification"
_local_app_data = os.getenv("LOCALAPPDATA")
if _local_app_data:
    USER_DATA_DIR = Path(_local_app_data) / APP_DATA_FOLDER
else:
    USER_DATA_DIR = Path.home() / ".sk_brickrescue" / "lego_set_verification"

# New app-managed credentials are stored per Windows user. Keep the project
# file as a read-only fallback so existing source checkouts continue to work.
env_path = USER_DATA_DIR / ".env"
DATA_DIR = PROJECT_ROOT / "data"
BRICKOGNIZE_RESULTS_DIR = DATA_DIR / "brickognize_results"
REPORTS_DIR = PROJECT_ROOT / "reports"
JOBS_DIR = PROJECT_ROOT / "saved_jobs"
relationships_csv_path = DATA_DIR / "part_relationships.csv"
relationships_download_url = (
    "https://cdn.rebrickable.com/media/downloads/"
    "part_relationships.csv.gz"
)
app_settings_path = DATA_DIR / "app_settings.json"

# Cached BrickLink -> Rebrickable translations, refreshed from the API on demand.
color_map_csv_path = DATA_DIR / "color_map.csv"
part_map_csv_path = DATA_DIR / "part_map.csv"
