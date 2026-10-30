'''
Rebrickable API access: authentication, caching for sets, parts, and minifigures.
Everything in this file talks to the network or the local cache.

- rebrickable_get() makes a single GET request to the API.
- get_paginated_results() handles paginated results for list endpoints.
- get_set_details() fetches the set details for a given set number.
- get_set_parts() fetches all parts in a set, including minifig parts.
- get_set_minifigs() fetches all minifigures in a set.
- get_parts_bulk_df() fetches details for multiple part numbers and returns 
a clean dataframe.
- get_part_colors_df() Fetch all available colors for one part and return a clean dataframe.
- get_colors_df() fetches every Rebrickable color with its BrickLink equivalents.
- find_part_by_bricklink_id() resolves a BrickLink part number to a Rebrickable one.
'''

import time
import requests
import requests_cache
import pandas as pd
from src.api_credentials import load_api_key, normalize_api_key

# =============================================================================
# Load the API key without copying it into the process environment.
# =============================================================================
rebrickable_api_key, rebrickable_api_key_source = load_api_key()
base_url = "https://rebrickable.com/api/v3/lego"
API_REQUEST_TIMEOUT = (10, 60)

# Store cache in local SQLite database to prevent repeated requests
session = requests_cache.CachedSession(
    # creates rebrickable_cache.sqlite
    cache_name="rebrickable_cache",   
    
    backend="sqlite",
    
    # cache each response for 24 hours
    expire_after=60 * 60 * 24, 
    
    # only cache successful responses, not errors
    allowable_codes=[200])

if rebrickable_api_key:
    print("API key detected. API path available.")
else:
    print("No API key detected. Using local datasets.")


def configure_rebrickable_api_key(api_key, source="user_file"):
    """Activate a newly saved key immediately, without restarting the app."""
    global rebrickable_api_key, rebrickable_api_key_source
    rebrickable_api_key = normalize_api_key(api_key)
    rebrickable_api_key_source = source if rebrickable_api_key else "missing"
    return rebrickable_api_key


def get_rebrickable_api_key_status():
    """Return non-secret configuration details for the desktop UI."""
    return {
        "configured": bool(rebrickable_api_key),
        "source": rebrickable_api_key_source,
    }


def _authorization_headers(api_key=None):
    key = rebrickable_api_key if api_key is None else normalize_api_key(api_key)
    if not key:
        raise ValueError("Missing Rebrickable API key.")
    return {"Authorization": f"key {key}"}

# =============================================================================
# Rebrickable request helper and error handling
# =============================================================================
class RebrickableAPIError(Exception):
    """Base error for Rebrickable API request problems."""
    pass

class SetNotFoundError(RebrickableAPIError):
    """Raised when a requested set number is not found."""
    pass

def _request_response_error(response, url=None):
    """Raise the existing friendly API errors for any HTTP response."""
    if response.status_code == 404:
        if url:
            raise SetNotFoundError(f"Nothing found at {url} — check the number.")
        raise RebrickableAPIError("Rebrickable could not find that resource.")
    if response.status_code == 401:
        raise RebrickableAPIError(
            "API key rejected — open Settings > Rebrickable API Key."
        )
    if response.status_code == 429:
        raise RebrickableAPIError("Rate limit hit. Wait before retrying.")

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RebrickableAPIError(f"Rebrickable returned {response.status_code}: {e}") from e


def _request(url, params=None, headers=None):
    response = session.get(
        url,
        headers=headers,
        params=params,
        timeout=API_REQUEST_TIMEOUT,
    )
    _request_response_error(response, url=url)

    return response

# =============================================================================
# Make one GET request to Rebrickable
# =============================================================================
def rebrickable_get(endpoint, params=None):
    url = f"{base_url}/{endpoint.lstrip('/')}"
    headers = _authorization_headers()

    response = _request(url, headers=headers, params=params)
    return response.json()

# =============================================================================
# Get all pages from a Rebrickable list endpoint
# =============================================================================
def get_paginated_results(endpoint, params=None, sleep_seconds=1.05):
    if params is None:
        params = {}

    all_results = []
    url = f"{base_url}/{endpoint.lstrip('/')}"
    headers = _authorization_headers()

    while url:
        response = _request(url, headers=headers, params=params)
        data = response.json()
        all_results.extend(data.get("results", []))
        
        url = data.get("next")
        # next URL already contains the query params
        params = None  

        if url:
            time.sleep(sleep_seconds)
    return all_results


def validate_rebrickable_api_key(api_key):
    """Validate a candidate key with a direct, uncached API request."""
    headers = _authorization_headers(api_key)
    try:
        response = requests.get(
            f"{base_url}/colors/",
            headers=headers,
            params={"page_size": 1},
            timeout=20,
        )
    except requests.exceptions.RequestException as exc:
        raise RebrickableAPIError(
            "Could not reach Rebrickable. Check the internet connection and "
            "try again."
        ) from exc

    _request_response_error(response)
    return True

# =============================================================================
# Rebrickable set numbers usually include a version suffix, such as '10475-1' 
# If the user types '10475', this assumes '10475-1'
# =============================================================================
def normalize_set_num(set_num):
    set_num = str(set_num).strip()
    
    if "-" not in set_num:
        set_num = f"{set_num}-1"
    
    return set_num


# =============================================================================
# Get set details
# =============================================================================
def get_set_details(set_num):
    set_num = normalize_set_num(set_num)
    return rebrickable_get(f"sets/{set_num}/")

# =============================================================================
# Get parts in a set
# =============================================================================
def get_set_parts(set_num, include_minifig_parts=True):
    set_num = normalize_set_num(set_num)

    params = {
        "page_size": 1000,
        "inc_part_details": 1,
        "inc_color_details": 1,
        "inc_minifig_parts": int(include_minifig_parts)}
    results = get_paginated_results(f"sets/{set_num}/parts/", params=params)
    rows = []
    for item in results:
        part = item.get("part", {})
        color = item.get("color", {})
        rows.append({
            "inventory_part_id": item.get("id"),
            "part_num": part.get("part_num"),
            "part_name": part.get("name"),
            "color_id": color.get("id"),
            "color_name": color.get("name"),
            "quantity_required": item.get("quantity"),
            "is_spare": item.get("is_spare"),
            "element_id": item.get("element_id"),
            "image_url": part.get("part_img_url")})

    return pd.DataFrame(rows)

# =============================================================================
# Get minifigures in a set
# =============================================================================
def get_set_minifigs(set_num):
    set_num = normalize_set_num(set_num)

    results = get_paginated_results(
        f"sets/{set_num}/minifigs/",
        params={"page_size": 1000})
    rows = []
    for item in results:
        rows.append({
            "fig_num": item.get("set_num"),
            "fig_name": item.get("set_name") or item.get("name"),
            "quantity": item.get("quantity"),
            "num_parts": item.get("num_parts"),
            "image_url": item.get("set_img_url")})

    return pd.DataFrame(rows)

# =============================================================================
# Fetch details for multiple part numbers from Rebrickable.
# Returns raw API results as a list of dictionaries.
# =============================================================================
def get_parts_bulk(part_nums, inc_part_details=True, chunk_size=100):
    clean_part_nums = (
        pd.Series(part_nums)
        .dropna()
        .astype(str)
        .str.strip())
    clean_part_nums = clean_part_nums[clean_part_nums != ""].unique().tolist()
    if not clean_part_nums:
        return []

    # part_nums travels in the query string, so a large set has to be split
    # across several requests rather than one oversized URL.
    all_results = []

    for start in range(0, len(clean_part_nums), chunk_size):
        chunk = clean_part_nums[start:start + chunk_size]
        params = {
            "part_nums": ",".join(chunk),
            "inc_part_details": int(inc_part_details)}
        all_results.extend(get_paginated_results("parts/", params=params))

    return all_results


# =============================================================================
# Fetch the BrickLink part number for multiple Rebrickable parts.
#
# Rebrickable only records an external ID when it has one, and for most plain
# elements the two catalogs use the same number, so the caller should treat a
# missing mapping as "the numbers match".
# =============================================================================
def get_parts_external_ids_df(part_nums):
    results = get_parts_bulk(part_nums, inc_part_details=True)

    columns = [
        "part_num",
        "part_name",
        "bricklink_part_num",
        "bricklink_part_candidates",
    ]

    if not results:
        return pd.DataFrame(columns=columns)

    rows = []

    for part in results:
        external_ids = part.get("external_ids") or {}
        bricklink_ids = []
        for external_id in external_ids.get("BrickLink") or []:
            candidate = str(external_id).strip()
            if candidate and candidate not in bricklink_ids:
                bricklink_ids.append(candidate)

        rows.append({
            "part_num": part.get("part_num"),
            "part_name": part.get("name"),
            "bricklink_part_num": (
                bricklink_ids[0] if len(bricklink_ids) == 1 else ""
            ),
            "bricklink_part_candidates": bricklink_ids,
        })

    return pd.DataFrame(rows, columns=columns)


# =============================================================================
# Fetch details for multiple parts and return a clean dataframe
# =============================================================================
def get_parts_bulk_df(part_nums, inc_part_details=True):
    results = get_parts_bulk(
        part_nums=part_nums,
        inc_part_details=inc_part_details)
    if not results:
        return pd.DataFrame()
    rows = []
    for part in results:
        rows.append({
            "part_num": part.get("part_num"),
            "part_name": part.get("name"),
            "part_category_id": part.get("part_cat_id"),
            "part_url": part.get("part_url"),
            "image_url": part.get("part_img_url")})
    return pd.DataFrame(rows)

# =============================================================================
# Fetch all colors in which one part appears in official sets.
# Returns raw API results.
# =============================================================================
def get_part_colors(part_num):
    part_num = str(part_num).strip()

    if not part_num:
        return []

    return get_paginated_results(
        f"parts/{part_num}/colors/",
        params={"page_size": 1000},
    )

# =============================================================================
# Fetch all available colors for one part and return a clean dataframe.
# The image URL is specific to that part/color combination when available.
# =============================================================================
def get_part_colors_df(part_num):
    results = get_part_colors(part_num)

    columns = [
        "part_num",
        "color_id",
        "color_name",
        "num_sets",
        "num_set_parts",
        "image_url",
    ]

    if not results:
        return pd.DataFrame(columns=columns)

    rows = []

    for item in results:
        # Rebrickable currently returns color_id and color_name as
        # top-level fields. The fallback supports a nested color object too.
        color = item.get("color") or {}

        color_id = item.get(
            "color_id",
            color.get("id"),
        )
        color_name = item.get(
            "color_name",
            color.get("name"),
        )

        rows.append(
            {
                "part_num": str(part_num),
                "color_id": color_id,
                "color_name": color_name,
                "num_sets": item.get("num_sets"),
                "num_set_parts": item.get("num_set_parts"),
                "image_url": item.get("part_img_url"),
            }
        )

    colors_df = pd.DataFrame(rows, columns=columns)

    colors_df["color_id"] = pd.to_numeric(
        colors_df["color_id"],
        errors="coerce",
    ).astype("Int64")

    return colors_df

# =============================================================================
# Fetch the full Rebrickable color list with its external BrickLink IDs.
# One BrickLink ID can appear on several Rebrickable colors, so this returns
# one row per Rebrickable/BrickLink pair rather than a unique key.
# =============================================================================
def get_colors_df():
    results = get_paginated_results("colors/", params={"page_size": 1000})

    columns = [
        "rebrickable_color_id",
        "rebrickable_color_name",
        "bricklink_color_id",
        "bricklink_color_name",
        "is_trans",
    ]

    if not results:
        return pd.DataFrame(columns=columns)

    rows = []

    for color in results:
        bricklink = (color.get("external_ids") or {}).get("BrickLink") or {}
        external_ids = bricklink.get("ext_ids") or []
        # ext_descrs is a list of name lists, positionally aligned with ext_ids.
        external_names = bricklink.get("ext_descrs") or []

        for position, external_id in enumerate(external_ids):
            if external_id is None:
                continue

            if position < len(external_names):
                names = external_names[position] or []
            else:
                names = []

            rows.append({
                "rebrickable_color_id": color.get("id"),
                "rebrickable_color_name": color.get("name"),
                "bricklink_color_id": external_id,
                "bricklink_color_name": names[0] if names else "",
                "is_trans": bool(color.get("is_trans")),
            })

    colors_df = pd.DataFrame(rows, columns=columns)

    for column in ["rebrickable_color_id", "bricklink_color_id"]:
        colors_df[column] = pd.to_numeric(
            colors_df[column],
            errors="coerce",
        ).astype("Int64")

    return colors_df.dropna(subset=["rebrickable_color_id", "bricklink_color_id"])

# =============================================================================
# Resolve a BrickLink part number to its Rebrickable equivalent.
# Returns None when Rebrickable holds no mapping for that BrickLink ID.
# =============================================================================
def find_part_by_bricklink_id(bricklink_id):
    bricklink_id = str(bricklink_id).strip()

    if not bricklink_id:
        return None

    results = get_paginated_results(
        "parts/",
        params={
            "bricklink_id": bricklink_id,
            "inc_part_details": 1,
            "page_size": 100,
        },
    )

    if not results:
        return None

    part = results[0]

    return {
        "part_num": part.get("part_num"),
        "part_name": part.get("name"),
        "part_category_id": part.get("part_cat_id"),
        "image_url": part.get("part_img_url"),
    }
