"""
Load the BrickLink colors that are known for one LEGO part.

BrickLink publishes a "Color Images" page for every catalog part.  This
module reads the color IDs from that page and intersects them with the
colors supported by the application's review dropdown.  Results are
cached locally so opening an already-seen part does not require another
network request.

If BrickLink is temporarily unavailable, the caller receives the full
review-color list.  That fallback keeps manual review usable instead of
blocking an employee because of a network problem.
"""

# ---------------------------------------------------------------------------
# MAINTAINER MAP: PART-SPECIFIC BRICKLINK COLORS
# ---------------------------------------------------------------------------
# Purpose:
# - Request the BrickLink Color Images page for one identified Part ID.
# - Extract cataloged color IDs from image URLs and visible color names.
# - Intersect those results with BRICKLINK_REVIEW_COLORS.
# - Cache successful lists in results/part_color_cache.json.
# - Return the full supported list if BrickLink cannot be reached or parsed.
#
# Important relationships:
# - gui.py starts this lookup in a worker thread so Tkinter does not freeze.
# - The GUI receives (options, used_fallback); do not change that return shape
#   without updating gui.py.
# - Cache keys are Part IDs. Values map visible color names to ID strings.
# - A network or cache-write problem must not disable manual color correction.
#
# BrickLink has used multiple HTML layouts. Keep both extraction paths below:
# numeric IDs in image URLs and visible color names. Removing either path can
# make a valid page look empty after a website layout change.
# ---------------------------------------------------------------------------

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import requests

from bricklink_colors import BRICKLINK_REVIEW_COLORS


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = PROJECT_ROOT / "results" / "part_color_cache.json"

COLOR_PAGE = (
    "https://www.bricklink.com/catalogColors.asp"
    "?itemType=P&itemNo={part_id}"
)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/150.0 Safari/537.36"
    ),
    "Referer": "https://www.bricklink.com/",
}


# CLASS GUIDE: _VisibleTextParser
# Purpose: Collect the text displayed by an HTML page.
class _VisibleTextParser(HTMLParser):
    """
    Collect the text displayed by an HTML page.

    BrickLink has changed how its catalog images are represented in the
    page source over time. Reading visible text gives the application a
    second, independent way to identify the available color names.
    """

    # FUNCTION GUIDE: __init__
    # Purpose: Initialize the parser's ordered collection of visible text.
    def __init__(self):
        """Initialize the parser's ordered collection of visible text."""

        super().__init__()
        self.text_items = []

    # FUNCTION GUIDE: handle_data
    # Purpose: Save non-empty pieces of visible page text.
    def handle_data(self, data):
        """Save non-empty pieces of visible page text."""

        cleaned_text = " ".join(data.split())
        if cleaned_text:
            self.text_items.append(cleaned_text)


# FUNCTION GUIDE: _load_cache
# Purpose: Return the saved part-color cache, or an empty dictionary.
def _load_cache():
    """Return the saved part-color cache, or an empty dictionary."""

    if not CACHE_FILE.exists():
        return {}

    try:
        with CACHE_FILE.open("r", encoding="utf-8") as cache_file:
            cache = json.load(cache_file)
    except (json.JSONDecodeError, OSError):
        return {}

    return cache if isinstance(cache, dict) else {}


# FUNCTION GUIDE: _save_cache
# Purpose: Write the cache without preventing review if saving fails.
def _save_cache(cache):
    """Write the cache without preventing review if saving fails."""

    # Cache persistence is an optimization only. A read-only results folder or
    # temporary file error must not interrupt an employee's active review.
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_FILE.open("w", encoding="utf-8") as cache_file:
            json.dump(cache, cache_file, indent=4, sort_keys=True)
    except OSError as error:
        print(f"Part-color cache could not be saved: {error}")


# FUNCTION GUIDE: _extract_known_color_ids
# Purpose: Extract BrickLink color IDs from catalog image URLs in the page.
def _extract_known_color_ids(page_text, part_id):
    """
    Extract BrickLink color IDs from catalog image URLs in the page.

    A normal URL resembles:
        /ItemImage/PN/11/3022.png

    The expression deliberately accepts absolute or relative URLs and
    optional query text so small BrickLink page changes are less likely
    to break the lookup.
    """

    escaped_part_id = re.escape(str(part_id))
    pattern = re.compile(
        rf"ItemImage/PN/(\d+)/{escaped_part_id}\.png",
        re.IGNORECASE,
    )
    image_url_ids = set(pattern.findall(page_text))

    # The image URL is the most direct source because it already includes
    # BrickLink's numeric color ID. Use it whenever BrickLink provides it.
    if image_url_ids:
        return sorted(image_url_ids, key=int)

    # Some BrickLink responses no longer expose those image URLs in their
    # original form. In that case, read the visible color names appearing
    # before the "Part Color Codes" section and translate the names through
    # the application's verified BrickLink color dictionary.
    parser = _VisibleTextParser()
    parser.feed(page_text)

    available_ids = set()
    for text_item in parser.text_items:
        if "Part Color Codes" in text_item:
            break

        color_id = BRICKLINK_REVIEW_COLORS.get(text_item)
        if color_id:
            available_ids.add(str(color_id))

    return sorted(available_ids, key=int)


# FUNCTION GUIDE: get_part_color_options
# Purpose: Return ``(name_to_id, source)`` for the selected part.
def get_part_color_options(part_id, predicted_color="Unknown"):
    """
    Return ``(name_to_id, source)`` for the selected part.

    ``source`` is one of ``"BrickLink"``, ``"Cache"``, or ``"Fallback"``
    and lets the GUI explain where its choices came from.
    """

    all_colors = dict(BRICKLINK_REVIEW_COLORS)
    clean_part_id = str(part_id or "").strip()

    if not clean_part_id or clean_part_id == "Unknown":
        return all_colors, "Fallback"

    inverse_colors = {
        str(color_id): color_name
        for color_name, color_id in all_colors.items()
    }
    cache = _load_cache()
    cached_ids = cache.get(clean_part_id)

    if isinstance(cached_ids, list) and cached_ids:
        available = {
            inverse_colors[color_id]: color_id
            for color_id in cached_ids
            if color_id in inverse_colors
        }
        if available:
            if predicted_color in all_colors:
                available.setdefault(
                    predicted_color,
                    all_colors[predicted_color],
                )
            return available, "Cache"

    try:
        response = requests.get(
            COLOR_PAGE.format(part_id=quote(clean_part_id)),
            headers=REQUEST_HEADERS,
            timeout=8,
        )
        response.raise_for_status()
        known_ids = _extract_known_color_ids(
            response.text,
            clean_part_id,
        )
    except requests.RequestException as error:
        print(f"BrickLink part-color lookup failed: {error}")
        return all_colors, "Fallback"

    available = {
        inverse_colors[color_id]: color_id
        for color_id in known_ids
        if color_id in inverse_colors
    }

    if not available:
        # This is not a network error: BrickLink returned a page, but its
        # contents could not be interpreted. Logging it prevents a silent
        # fallback from being mistaken for a successful part-specific list.
        print(
            "BrickLink returned no recognized colors for "
            f"Part {clean_part_id}; using the fallback list."
        )
        return all_colors, "Fallback"

    cache[clean_part_id] = known_ids
    _save_cache(cache)

    # Keep the original prediction visible even if BrickLink's page has
    # not yet listed an image for that exact color.
    if predicted_color in all_colors:
        available.setdefault(
            predicted_color,
            all_colors[predicted_color],
        )

    return available, "BrickLink"
