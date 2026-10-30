"""
Save, reload, and synchronize employee review decisions.

Brickognize predictions are never discarded. A manual decision is stored in
``brickognize_reviews.json`` and copied into separate ``Final ...`` columns in
the CSV. Keeping original and final values side by side makes the process
auditable and allows the GUI and Excel report to show the accepted result.

This module contains no widgets. The GUI passes ordinary result dictionaries
into these functions after an employee confirms a color or part.
"""

# ---------------------------------------------------------------------------
# MAINTAINER MAP: REVIEW PERSISTENCE AND AUDIT HISTORY
# ---------------------------------------------------------------------------
# This module owns the boundary between an automatic prediction and an employee
# decision. Its central rule is: never overwrite the original prediction.
#
# Data flow:
# 1. gui.py sends the selected result and confirmed value here.
# 2. A review entry is added to brickognize_reviews.json.
# 3. sync_reviewed_result_to_csv() updates only the matching image row.
# 4. Original prediction columns stay unchanged.
# 5. Separate Final..., Approval Status, and Review Completed columns describe
#    the latest accepted operational result.
#
# Matching invariant:
# - Image filename is the persistent row/review key used by the existing app.
# - Source filenames should therefore be unique inside one active batch.
#
# Status invariant:
# - Auto-Accepted: no employee decision changed the automatic result.
# - Pending Review: confidence suggested attention and no decision exists.
# - Confirmed: the employee selected the same value as the prediction.
# - Corrected: the employee selected a different final value.
#
# If fields change here, also inspect main.py CSV headers, gui.py restore/display
# logic, excel_report.py columns, and the README output-field table.
# ---------------------------------------------------------------------------

import csv
import json
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared result locations and final CSV columns
# ---------------------------------------------------------------------------

# One shared file containing all completed manual reviews.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVIEW_FILE = PROJECT_ROOT / "results" / "brickognize_reviews.json"
CSV_FILE = PROJECT_ROOT / "results" / "brickognize_results.csv"

FINAL_CSV_FIELDS = [
    "Final Review Status",
    "Final Part ID",
    "Final Part Name",
    "Final Part Confidence",
    "Final LEGO Color",
    "Final BrickLink Color ID",
    "Part Approval Status",
    "Color Approval Status",
    "Part Review Completed",
    "Color Review Completed",
    "Final BrickLink URL",
]


# ---------------------------------------------------------------------------
# CSV synchronization
# ---------------------------------------------------------------------------

# FUNCTION GUIDE: sync_reviewed_result_to_csv
# Purpose: Add the employee-reviewed values to the matching CSV row.
def sync_reviewed_result_to_csv(result):
    """
    Add the employee-reviewed values to the matching CSV row.

    Original Brickognize columns are deliberately left unchanged for
    traceability. The separate Final columns show the current operational
    decision after any part or color review.
    """

    if not CSV_FILE.exists() or CSV_FILE.stat().st_size == 0:
        return False

    with CSV_FILE.open(
        "r",
        newline="",
        encoding="utf-8",
    ) as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if not fieldnames:
        return False

    # Older CSV files may predate newer Final columns. Appending missing headers
    # migrates them in place without discarding existing employee rows.
    for field_name in FINAL_CSV_FIELDS:
        if field_name not in fieldnames:
            fieldnames.append(field_name)

    part_review_required = (
        result.get("Part Review Required") == "Yes"
    )
    color_review_required = (
        result.get("Color Review Required") == "Yes"
    )

    if part_review_required and color_review_required:
        final_status = "Needs Part and Color Review"
    elif part_review_required:
        final_status = "Needs Part Review"
    elif color_review_required:
        final_status = "Needs Color Review"
    else:
        final_status = "Ready"

    part_review_completed = result.get(
        "Part Review Completed",
        "No",
    )
    color_review_completed = result.get(
        "Color Review Completed",
        "No",
    )

    if part_review_required:
        part_approval_status = "Pending Review"
    elif part_review_completed == "Yes":
        part_approval_status = result.get(
            "Part Approval Status",
            "Confirmed",
        )
    else:
        part_approval_status = "Auto-Accepted"

    if color_review_required:
        color_approval_status = "Pending Review"
    elif color_review_completed == "Yes":
        color_approval_status = result.get(
            "Color Approval Status",
            "Confirmed",
        )
    else:
        color_approval_status = "Auto-Accepted"

    final_values = {
        "Final Review Status": final_status,
        "Final Part ID": result.get("Part ID", ""),
        "Final Part Name": result.get("Part Name", ""),
        "Final Part Confidence": result.get(
            "Part Confidence",
            "",
        ),
        "Final LEGO Color": result.get("LEGO Color", ""),
        "Final BrickLink Color ID": result.get(
            "Confirmed BrickLink Color ID",
            result.get("BrickLink Color ID", ""),
        ),
        "Part Approval Status": part_approval_status,
        "Color Approval Status": color_approval_status,
        "Part Review Completed": part_review_completed,
        "Color Review Completed": color_review_completed,
        "Final BrickLink URL": result.get("BrickLink URL", ""),
    }

    image_name = result.get("Image", "")
    matching_row = None

    for row in rows:
        if row.get("Image") == image_name:
            matching_row = row
            break

    if matching_row is None:
        return False

    changed = any(
        matching_row.get(field_name, "") != str(value)
        for field_name, value in final_values.items()
    )

    if not changed and all(
        field_name in (reader.fieldnames or [])
        for field_name in FINAL_CSV_FIELDS
    ):
        return True

    matching_row.update(final_values)

    temporary_file = CSV_FILE.with_suffix(".csv.tmp")

    with temporary_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    temporary_file.replace(CSV_FILE)
    return True


# ---------------------------------------------------------------------------
# Color-review history
# ---------------------------------------------------------------------------

# FUNCTION GUIDE: save_color_review
# Purpose: Save an employee's manual color-review decision.
def save_color_review(
    result,
    confirmed_color,
    confirmed_color_id,
):
    """
    Save an employee's manual color-review decision.

    The original Brickognize prediction is preserved so it can be
    compared with the employee-confirmed color.
    """

    REVIEW_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    saved_reviews = {}

    if REVIEW_FILE.exists():
        try:
            with REVIEW_FILE.open(
                "r",
                encoding="utf-8",
            ) as review_file:
                saved_reviews = json.load(review_file)

            if not isinstance(saved_reviews, dict):
                saved_reviews = {}

        except (json.JSONDecodeError, OSError):
            saved_reviews = {}

    image_name = result.get("Image", "Unknown")

    review_record = {
        "Image": image_name,
        "Part ID": result.get("Part ID", "Unknown"),
        "Part Name": result.get("Part Name", "Unknown"),
        "Original Color": result.get(
            "Original LEGO Color",
            result.get(
                "Predicted LEGO Color",
                result.get("LEGO Color", "Unknown"),
            ),
        ),
        "Original BrickLink Color ID": result.get(
            "Predicted BrickLink Color ID",
            result.get("BrickLink Color ID", ""),
        ),
        "Confirmed Color": confirmed_color,
        "Confirmed BrickLink Color ID": confirmed_color_id,
        "Original Color Confidence": result.get(
            "Color Confidence",
            "Unknown",
        ),
        "Color Review Completed": "Yes",
        "Color Approval Status": (
            "Confirmed"
            if confirmed_color == result.get(
                "Original LEGO Color",
                result.get(
                    "Predicted LEGO Color",
                    result.get("LEGO Color", "Unknown"),
                ),
            )
            else "Corrected"
        ),
        "Reviewed At": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
    }

    existing_review = saved_reviews.get(
        image_name,
        {},
    )

    if not isinstance(existing_review, dict):
        existing_review = {}

    # Convert an older flat color review into the new grouped format.
    if (
        "Confirmed Color" in existing_review
        and "Color Review" not in existing_review
    ):
        existing_review = {
            "Color Review": existing_review
        }

    existing_review["Color Review"] = review_record
    saved_reviews[image_name] = existing_review

    with REVIEW_FILE.open(
        "w",
        encoding="utf-8",
    ) as review_file:
        json.dump(
            saved_reviews,
            review_file,
            indent=4,
        )

    return review_record


# FUNCTION GUIDE: load_color_review
# Purpose: Load a previously completed color review by image filename.
def load_color_review(image_name):
    """Load a previously completed color review by image filename."""

    if not REVIEW_FILE.exists():
        return None

    try:
        with REVIEW_FILE.open(
            "r",
            encoding="utf-8",
        ) as review_file:
            saved_reviews = json.load(review_file)

    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(saved_reviews, dict):
        return None

    saved_review = saved_reviews.get(image_name)

    if not isinstance(saved_review, dict):
        return None

    # Read the new grouped format.
    if "Color Review" in saved_review:
        return saved_review["Color Review"]

    # Continue supporting reviews saved before the format changed.
    if "Confirmed Color" in saved_review:
        return saved_review

    return None


def clear_color_review(image_name):
    """Remove an obsolete color decision after accepting a new rescan image."""

    if not REVIEW_FILE.exists():
        return
    try:
        with REVIEW_FILE.open("r", encoding="utf-8") as review_file:
            saved_reviews = json.load(review_file)
    except (json.JSONDecodeError, OSError):
        return
    entry = saved_reviews.get(image_name)
    if not isinstance(entry, dict):
        return
    entry.pop("Color Review", None)
    # Support and remove an older flat color-review representation too.
    for field_name in (
        "Confirmed Color",
        "Confirmed BrickLink Color ID",
        "Original Color",
        "Color Review Completed",
        "Color Approval Status",
    ):
        entry.pop(field_name, None)
    if entry:
        saved_reviews[image_name] = entry
    else:
        saved_reviews.pop(image_name, None)
    with REVIEW_FILE.open("w", encoding="utf-8") as review_file:
        json.dump(saved_reviews, review_file, indent=4)


# ---------------------------------------------------------------------------
# Part-review history
# ---------------------------------------------------------------------------

# FUNCTION GUIDE: save_part_review
# Purpose: Save an employee's decision after comparing two part scans.
def save_part_review(
    original_result,
    rescan_result,
    rescan_image_path,
    decision,
):
    """Save an employee's decision after comparing two part scans."""

    REVIEW_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    saved_reviews = {}

    if REVIEW_FILE.exists():
        try:
            with REVIEW_FILE.open(
                "r",
                encoding="utf-8",
            ) as review_file:
                saved_reviews = json.load(review_file)

            if not isinstance(saved_reviews, dict):
                saved_reviews = {}

        except (json.JSONDecodeError, OSError):
            saved_reviews = {}

    image_name = original_result.get("Image", "Unknown")

    if decision == "Use Rescan Result":
        final_result = rescan_result
    else:
        final_result = original_result

    part_review_record = {
        "Original Image": image_name,
        "Original Part ID": original_result.get(
            "Part ID",
            "Unknown",
        ),
        "Original Part Name": original_result.get(
            "Part Name",
            "Unknown",
        ),
        "Original Part Confidence": original_result.get(
            "Part Confidence",
            "Unknown",
        ),
        "Rescan Image": Path(rescan_image_path).name,
        "Rescan Image Path": str(Path(rescan_image_path).resolve()),
        "Rescan Part ID": rescan_result.get(
            "Part ID",
            "Unknown",
        ),
        "Rescan Part Name": rescan_result.get(
            "Part Name",
            "Unknown",
        ),
        "Rescan Part Confidence": rescan_result.get(
            "Part Confidence",
            "Unknown",
        ),
        "Decision": decision,
        "Final Part ID": final_result.get(
            "Part ID",
            "Unknown",
        ),
        "Final Part Name": final_result.get(
            "Part Name",
            "Unknown",
        ),
        "Final Part Confidence": final_result.get(
            "Part Confidence",
            "Unknown",
        ),
        "Part Review Completed": "Yes",
        "Part Approval Status": (
            "Corrected"
            if decision == "Use Rescan Result"
            else "Confirmed"
        ),
        "Rescan LEGO Color": rescan_result.get("LEGO Color", "Unknown"),
        "Rescan BrickLink Color ID": rescan_result.get("BrickLink Color ID", ""),
        "Rescan Color Confidence": rescan_result.get("Color Confidence", "Unknown"),
        "Reviewed At": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
    }

    existing_review = saved_reviews.get(
        image_name,
        {},
    )

    if not isinstance(existing_review, dict):
        existing_review = {}

    # Preserve an older flat color review before adding part review.
    if (
        "Confirmed Color" in existing_review
        and "Color Review" not in existing_review
    ):
        existing_review = {
            "Color Review": existing_review
        }

    existing_review["Part Review"] = part_review_record
    saved_reviews[image_name] = existing_review

    with REVIEW_FILE.open(
        "w",
        encoding="utf-8",
    ) as review_file:
        json.dump(
            saved_reviews,
            review_file,
            indent=4,
        )

    return part_review_record


# FUNCTION GUIDE: load_part_review
# Purpose: Load a previously completed part-review decision.
def load_part_review(image_name):
    """Load a previously completed part-review decision."""

    if not REVIEW_FILE.exists():
        return None

    try:
        with REVIEW_FILE.open(
            "r",
            encoding="utf-8",
        ) as review_file:
            saved_reviews = json.load(review_file)

    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(saved_reviews, dict):
        return None

    saved_review = saved_reviews.get(image_name)

    if not isinstance(saved_review, dict):
        return None

    part_review = saved_review.get("Part Review")

    if not isinstance(part_review, dict):
        return None

    return part_review
