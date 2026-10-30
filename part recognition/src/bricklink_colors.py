"""
Common solid LEGO colors used during manual review.

Each dictionary value is BrickLink's color ID. The ID allows the
application to load the selected catalog image and open the correct
color on BrickLink after an employee confirms the selection.
"""

# ---------------------------------------------------------------------------
# MAINTAINER MAP: REVIEW COLOR REFERENCE
# ---------------------------------------------------------------------------
# This module intentionally contains data only. Keeping the supported colors in
# one location prevents the GUI, BrickLink lookup, review saver, and report
# builder from maintaining conflicting color lists.
#
# Dictionary key   = employee-facing BrickLink color name.
# Dictionary value = BrickLink's numeric catalog color ID stored as text.
#
# Maintenance rules:
# 1. Use BrickLink's visible spelling for every key because the HTML parser
#    matches visible names against this dictionary.
# 2. Keep IDs as strings. URLs and CSV/JSON output treat them as identifiers,
#    not mathematical values or confidence percentages.
# 3. Verify every new or changed ID against BrickLink before distribution.
# 4. Adding a color here makes it available to the fallback review dropdown.
# 5. Removing or renaming a color can affect old reviews, cached options,
#    BrickLink links, and Excel output; test all four areas afterward.
# 6. The grouping comments below affect readability only, not program logic.
# ---------------------------------------------------------------------------

BRICKLINK_REVIEW_COLORS = {
    # Neutral colors
    "White": "1",
    "Light Bluish Gray": "86",
    "Dark Bluish Gray": "85",
    "Black": "11",

    # Red, orange, and brown colors
    "Dark Red": "59",
    "Red": "5",
    "Reddish Orange": "167",
    "Coral": "220",
    "Dark Brown": "120",
    "Reddish Brown": "88",
    "Dark Tan": "69",
    "Tan": "2",
    "Light Nougat": "90",
    "Nougat": "28",
    "Medium Nougat": "150",
    "Dark Orange": "68",
    "Orange": "4",
    "Bright Light Orange": "110",

    # Yellow and green colors
    "Yellow": "3",
    "Bright Light Yellow": "103",
    "Lime": "34",
    "Olive Green": "155",
    "Dark Green": "80",
    "Green": "6",
    "Bright Green": "36",
    "Sand Green": "48",
    "Dark Turquoise": "39",
    "Aqua": "41",
    "Light Aqua": "152",

    # Blue colors
    "Dark Blue": "63",
    "Blue": "7",
    "Dark Azure": "153",
    "Medium Azure": "156",
    "Medium Blue": "42",
    "Bright Light Blue": "105",
    "Light Blue": "62",
    "Sand Blue": "55",

    # Purple and pink colors
    "Dark Purple": "89",
    "Purple": "24",
    "Medium Lavender": "157",
    "Lavender": "154",
    "Magenta": "71",
    "Dark Pink": "47",
    "Bright Pink": "104",
    "Pink": "23",
}
