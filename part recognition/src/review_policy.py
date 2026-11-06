"""Shared conservative review rules for new and restored predictions."""

import math

PART_REVIEW_THRESHOLD = 80.0
COLOR_REVIEW_THRESHOLD = 80.0


def valid_identifier(value):
    return value is not None and str(value).strip().casefold() not in {
        "", "unknown", "n/a", "none", "nan", "null", "-",
    }


def has_identifiers(result):
    return bool(result) and valid_identifier(result.get("Part ID")) and str(result.get(
        "Confirmed BrickLink Color ID", result.get("BrickLink Color ID")
    )).strip().isdecimal()


def _high_confidence(value, threshold):
    try:
        score = float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(score) and threshold <= score <= 100


def apply_review_policy(result):
    """Keep manual decisions, but never accept missing identifiers."""
    part_required = not valid_identifier(result.get("Part ID")) or (
        result.get("Part Review Completed") != "Yes"
        and (result.get("Part Review Required") == "Yes" or not _high_confidence(
            result.get("Part Confidence"), PART_REVIEW_THRESHOLD
        ))
    )
    color_required = not str(result.get(
        "Confirmed BrickLink Color ID", result.get("BrickLink Color ID")
    )).strip().isdecimal() or (
        result.get("Color Review Completed") != "Yes"
        and (result.get("Color Review Required") == "Yes"
             or result.get("Color Method") != "Brickognize Color Prediction"
             or not _high_confidence(result.get("Color Confidence"), COLOR_REVIEW_THRESHOLD))
    )
    for kind, required in (("Part", part_required), ("Color", color_required)):
        result[f"{kind} Review Required"] = "Yes" if required else "No"
        if required:
            result[f"{kind} Approval Status"] = "Pending Review"
        elif result.get(f"{kind} Review Completed") != "Yes":
            result[f"{kind} Approval Status"] = "Auto-Accepted"
    result["Final Review Status"] = (
        "Needs Part and Color Review" if part_required and color_required else
        "Needs Part Review" if part_required else
        "Needs Color Review" if color_required else "Ready"
    )
    return result


def needs_attention(result):
    return not result or apply_review_policy(result)["Final Review Status"] != "Ready"


def review_group(result):
    """Group the currently displayed identifications, including saved corrections."""
    if not has_identifiers(result):
        return ("", "")
    return (str(result["Part ID"]), str(result.get(
        "Confirmed BrickLink Color ID", result.get("BrickLink Color ID")
    )))
