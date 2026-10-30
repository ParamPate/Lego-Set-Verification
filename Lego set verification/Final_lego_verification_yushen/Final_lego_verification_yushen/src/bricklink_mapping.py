'''
Translate between BrickLink and Rebrickable identifiers.

Rebrickable is the internal key for this project because the official set
inventory and part_relationships.csv are both Rebrickable-keyed. BrickLink
appears at the two edges: Brickognize exports arrive as BrickLink, and reports
go back out as BrickLink so they can be uploaded to a BrickLink Wanted List.

Both directions come from Rebrickable's own external-ID data and are cached in
data/ so repeat imports do not re-query the API.

  - load_color_map() returns every BrickLink/Rebrickable color pair.
  - map_bricklink_color_ids() / map_rebrickable_color_ids() translate colors.
  - resolve_bricklink_part_nums() translates BrickLink parts to Rebrickable.
  - resolve_rebrickable_part_nums() translates Rebrickable parts to BrickLink.
'''

import json
import os
import shutil
import tempfile

import pandas as pd

from config import color_map_csv_path, part_map_csv_path
from src.api_rebrickable import (
    RebrickableAPIError,
    find_part_by_bricklink_id,
    get_colors_df,
    get_parts_bulk_df,
    get_parts_external_ids_df,
)

color_map_columns = [
    "bricklink_color_id",
    "rebrickable_color_id",
    "bricklink_color_name",
    "rebrickable_color_name",
]

part_map_columns = [
    "bricklink_part_num",
    "rebrickable_part_num",
    "part_name",
    "resolved_via",
    "bricklink_part_candidates",
]

# How a part number translation was obtained.
DIRECT_MATCH = "direct"
BRICKLINK_MATCH = "bricklink_id"
EXTERNAL_ID_MATCH = "external_id"
MANUAL_MATCH = "manual_selection"
AMBIGUOUS_MATCH = "ambiguous_external_ids"
# A successful API response confirmed that Rebrickable has no BrickLink ID.
# Legacy "assumed" rows are intentionally not trusted because older versions
# also wrote that status after transient API failures.
ASSUMED_MATCH = "assumed_no_external_id"
# A successful direct and BrickLink-ID lookup found no Rebrickable part.
# Legacy "unresolved" rows are retried because older versions also wrote that
# status after transient failures in either API lookup.
UNRESOLVED = "unresolved_no_match"


def parse_bricklink_candidates(value):
    """Return unique BrickLink candidate IDs from a list or cached JSON."""
    if isinstance(value, (list, tuple, set, pd.Series)):
        raw_candidates = list(value)
    elif value is None:
        raw_candidates = []
    else:
        try:
            if pd.isna(value):
                return []
        except (TypeError, ValueError):
            pass

        text = str(value).strip()
        if not text:
            raw_candidates = []
        else:
            try:
                decoded = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = text
            raw_candidates = (
                list(decoded)
                if isinstance(decoded, (list, tuple, set))
                else [decoded]
            )

    result = []
    for raw_candidate in raw_candidates:
        if raw_candidate is None:
            continue
        candidate = str(raw_candidate).strip()
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _serialize_bricklink_candidates(value):
    candidates = parse_bricklink_candidates(value)
    return json.dumps(candidates) if candidates else ""


# ============================== Colors =======================================
def refresh_color_map():
    """Rebuild the cached color table from the Rebrickable API."""
    colors_df = get_colors_df()

    if colors_df.empty:
        return pd.DataFrame(columns=color_map_columns)

    color_map_df = (
        colors_df[color_map_columns]
        .dropna(subset=["bricklink_color_id", "rebrickable_color_id"])
        .drop_duplicates()
    )

    if color_map_df.empty:
        return pd.DataFrame(columns=color_map_columns)

    color_map_csv_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            suffix=".tmp",
            dir=color_map_csv_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            color_map_df.to_csv(temporary_file, index=False)

        if color_map_csv_path.exists():
            backup_path = color_map_csv_path.with_suffix(
                f"{color_map_csv_path.suffix}.bak"
            )
            shutil.copy2(color_map_csv_path, backup_path)

        os.replace(temporary_path, color_map_csv_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    return color_map_df.reset_index(drop=True)


def load_color_map(refresh=False):
    """
    Return every BrickLink/Rebrickable color pair.

    The table is stored uncollapsed because the relationship is not strictly
    one-to-one, and each direction needs its own tie-break.
    """
    if not refresh and color_map_csv_path.exists():
        cached_df = pd.read_csv(color_map_csv_path, encoding="utf-8")

        if set(color_map_columns).issubset(cached_df.columns):
            for column in ["bricklink_color_id", "rebrickable_color_id"]:
                cached_df[column] = pd.to_numeric(
                    cached_df[column],
                    errors="coerce",
                ).astype("Int64")

            return cached_df[color_map_columns].dropna(
                subset=["bricklink_color_id", "rebrickable_color_id"]
            )

    return refresh_color_map()


def _collapse_color_map(color_map_df, key_column, value_column):
    """
    Reduce the pair table to one value per key.

    Prefer a pair whose two names agree, then the lowest value ID, so repeated
    runs always choose the same color.
    """
    if color_map_df.empty:
        return {}

    candidates = color_map_df.copy()

    candidates["_names_agree"] = (
        candidates["bricklink_color_name"].astype(str).str.strip().str.casefold()
        == candidates["rebrickable_color_name"].astype(str).str.strip().str.casefold()
    )

    candidates = candidates.sort_values(
        by=[key_column, "_names_agree", value_column],
        ascending=[True, False, True],
        kind="stable",
    ).drop_duplicates(subset=[key_column], keep="first")

    return dict(
        zip(
            candidates[key_column].tolist(),
            candidates[value_column].tolist(),
        )
    )


def _map_color_ids(color_ids, lookup):
    source_ids = pd.to_numeric(
        pd.Series(color_ids, dtype="object").reset_index(drop=True),
        errors="coerce",
    ).astype("Int64")

    return pd.Series(
        [
            lookup.get(color_id, pd.NA) if pd.notna(color_id) else pd.NA
            for color_id in source_ids.tolist()
        ],
        dtype="Int64",
    )


def map_bricklink_color_ids(bricklink_color_ids, color_map_df):
    """Translate BrickLink color IDs to Rebrickable ones. Misses become NA."""
    lookup = _collapse_color_map(
        color_map_df,
        "bricklink_color_id",
        "rebrickable_color_id",
    )
    return _map_color_ids(bricklink_color_ids, lookup)


def map_rebrickable_color_ids(rebrickable_color_ids, color_map_df):
    """Translate Rebrickable color IDs to BrickLink ones. Misses become NA."""
    lookup = _collapse_color_map(
        color_map_df,
        "rebrickable_color_id",
        "bricklink_color_id",
    )
    return _map_color_ids(rebrickable_color_ids, lookup)


# ============================== Parts ========================================
def load_part_map():
    """Return the cached part number translations."""
    if not part_map_csv_path.exists():
        return pd.DataFrame(columns=part_map_columns)

    cached_df = pd.read_csv(part_map_csv_path, encoding="utf-8", dtype=str)

    legacy_columns = part_map_columns[:-1]
    if not set(legacy_columns).issubset(cached_df.columns):
        return pd.DataFrame(columns=part_map_columns)

    for column in part_map_columns:
        if column not in cached_df.columns:
            cached_df[column] = ""

    return cached_df[part_map_columns].fillna("")


def save_part_map(part_map_df):
    part_map_csv_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            suffix=".tmp",
            dir=part_map_csv_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            part_map_df.to_csv(temporary_file, index=False)

        os.replace(temporary_path, part_map_csv_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def _merge_into_part_map(new_rows_df):
    if new_rows_df.empty:
        return

    normalized_new_rows = new_rows_df.copy()
    for column in part_map_columns:
        if column not in normalized_new_rows.columns:
            normalized_new_rows[column] = ""

    combined_df = pd.concat(
        [load_part_map(), normalized_new_rows[part_map_columns]],
        ignore_index=True,
    ).drop_duplicates(
        subset=["bricklink_part_num", "rebrickable_part_num"],
        keep="last",
    )

    save_part_map(combined_df)


def save_manual_part_mapping(
        rebrickable_part_num,
        bricklink_part_num,
        part_name="",
        candidates=None):
    """Persist an operator-confirmed choice from ambiguous BrickLink IDs."""
    rebrickable_part_num = str(rebrickable_part_num).strip()
    bricklink_part_num = str(bricklink_part_num).strip()
    if not rebrickable_part_num or not bricklink_part_num:
        raise ValueError("Both Rebrickable and BrickLink part numbers are required.")

    candidate_list = parse_bricklink_candidates(candidates)
    if candidate_list and bricklink_part_num not in candidate_list:
        raise ValueError("The selected BrickLink ID is not one of the candidates.")

    _merge_into_part_map(pd.DataFrame([{
        "bricklink_part_num": bricklink_part_num,
        "rebrickable_part_num": rebrickable_part_num,
        "part_name": str(part_name or ""),
        "resolved_via": MANUAL_MATCH,
        "bricklink_part_candidates": _serialize_bricklink_candidates(
            candidate_list
        ),
    }], columns=part_map_columns))


def _clean_part_nums(part_nums):
    cleaned = (
        pd.Series(part_nums, dtype="object")
        .dropna()
        .astype(str)
        .str.strip()
    )
    return cleaned[cleaned != ""].unique().tolist()


def _bulk_direct_matches(part_nums):
    """Return the subset of numbers Rebrickable already recognises as its own."""
    if not part_nums:
        return {}

    found_df = get_parts_bulk_df(part_nums, inc_part_details=True)

    if found_df.empty:
        return {}

    return dict(
        zip(
            found_df["part_num"].astype(str),
            found_df["part_name"].fillna("").astype(str),
        )
    )


def resolve_bricklink_part_nums(bricklink_part_nums, use_cache=True):
    """
    Translate BrickLink part numbers into Rebrickable part numbers.

    Most plain elements share a number between the two catalogs, so those are
    confirmed with a single bulk request. Only the leftovers - typically
    printed parts such as BrickLink 67095pb067 against Rebrickable's pr
    notation - need an individual lookup. An unresolved result is cached only
    after both API lookups complete successfully. API failures propagate to the
    caller and leave the entire requested batch uncached.

    Returns one row per unique input number with a resolved_via column.
    """
    requested = _clean_part_nums(bricklink_part_nums)

    if not requested:
        return pd.DataFrame(columns=part_map_columns)

    cached_df = (
        load_part_map() if use_cache else pd.DataFrame(columns=part_map_columns)
    )
    trusted_cache_methods = {
        DIRECT_MATCH,
        BRICKLINK_MATCH,
        EXTERNAL_ID_MATCH,
        MANUAL_MATCH,
        ASSUMED_MATCH,
        UNRESOLVED,
    }
    cached_rows = {
        row["bricklink_part_num"]: dict(row)
        for _, row in cached_df.iterrows()
        if (
            row["bricklink_part_num"]
            and row["resolved_via"] in trusted_cache_methods
        )
    }

    resolved_rows = []
    outstanding = []

    for part_num in requested:
        cached_row = cached_rows.get(part_num)

        if cached_row is not None:
            resolved_rows.append(cached_row)
        else:
            outstanding.append(part_num)

    if outstanding:
        direct_names = _bulk_direct_matches(outstanding)
        still_outstanding = []

        for part_num in outstanding:
            if part_num in direct_names:
                resolved_rows.append({
                    "bricklink_part_num": part_num,
                    "rebrickable_part_num": part_num,
                    "part_name": direct_names[part_num],
                    "resolved_via": DIRECT_MATCH,
                })
            else:
                still_outstanding.append(part_num)

        for part_num in still_outstanding:
            match = find_part_by_bricklink_id(part_num)

            if match and match.get("part_num"):
                resolved_rows.append({
                    "bricklink_part_num": part_num,
                    "rebrickable_part_num": str(match["part_num"]),
                    "part_name": str(match.get("part_name") or ""),
                    "resolved_via": BRICKLINK_MATCH,
                })
            else:
                resolved_rows.append({
                    "bricklink_part_num": part_num,
                    "rebrickable_part_num": "",
                    "part_name": "",
                    "resolved_via": UNRESOLVED,
                })

    part_map_df = pd.DataFrame(resolved_rows, columns=part_map_columns)

    if use_cache:
        _merge_into_part_map(part_map_df)

    return part_map_df


def resolve_rebrickable_part_nums(rebrickable_part_nums, use_cache=True):
    """
    Translate Rebrickable part numbers into BrickLink part numbers.

    Rebrickable stores an external BrickLink ID only where one exists, so a
    part with no mapping is reported as an assumed match and keeps its own
    number, which is correct for the majority of plain elements. An assumption
    is cached only after the API successfully confirms that no external ID is
    recorded. API failures propagate to the caller and are never cached.

    Returns one row per unique input number with a resolved_via column.
    """
    requested = _clean_part_nums(rebrickable_part_nums)

    if not requested:
        return pd.DataFrame(columns=part_map_columns)

    cached_df = (
        load_part_map() if use_cache else pd.DataFrame(columns=part_map_columns)
    )
    trusted_cache_methods = {
        DIRECT_MATCH,
        BRICKLINK_MATCH,
        EXTERNAL_ID_MATCH,
        MANUAL_MATCH,
        ASSUMED_MATCH,
        AMBIGUOUS_MATCH,
    }
    cached_rows = {
        row["rebrickable_part_num"]: dict(row)
        for _, row in cached_df.iterrows()
        if (
            row["rebrickable_part_num"]
            and row["resolved_via"] in trusted_cache_methods
            and (
                row["bricklink_part_num"]
                or (
                    row["resolved_via"] == AMBIGUOUS_MATCH
                    and len(parse_bricklink_candidates(
                        row["bricklink_part_candidates"]
                    )) > 1
                )
            )
        )
    }

    resolved_rows = []
    outstanding = []

    for part_num in requested:
        cached_row = cached_rows.get(part_num)

        if cached_row is not None:
            resolved_rows.append(cached_row)
        else:
            outstanding.append(part_num)

    if outstanding:
        external_df = get_parts_external_ids_df(outstanding)

        external_lookup = {}
        if not external_df.empty:
            external_lookup = {
                str(row["part_num"]): dict(row)
                for _, row in external_df.iterrows()
            }

        for part_num in outstanding:
            external_match = external_lookup.get(part_num)
            if external_match is None:
                raise RebrickableAPIError(
                    "Rebrickable did not return part details for "
                    f"{part_num}; no BrickLink mapping was cached."
                )

            selected_value = external_match.get("bricklink_part_num", "")
            bricklink_part_num = (
                ""
                if selected_value is None or pd.isna(selected_value)
                else str(selected_value).strip()
            )
            name_value = external_match.get("part_name", "")
            part_name = (
                ""
                if name_value is None or pd.isna(name_value)
                else str(name_value)
            )
            candidates = parse_bricklink_candidates(
                external_match.get("bricklink_part_candidates", "")
            )
            if not candidates and bricklink_part_num:
                candidates = [bricklink_part_num]

            if len(candidates) > 1:
                bricklink_part_num = ""
                resolved_via = AMBIGUOUS_MATCH
            elif candidates:
                bricklink_part_num = candidates[0]
                resolved_via = EXTERNAL_ID_MATCH
            else:
                # The successful API response recorded no external ID, so use
                # the shared catalog number as a verified assumption.
                bricklink_part_num = part_num
                resolved_via = ASSUMED_MATCH

            resolved_rows.append({
                "bricklink_part_num": bricklink_part_num,
                "rebrickable_part_num": part_num,
                "part_name": part_name,
                "resolved_via": resolved_via,
                "bricklink_part_candidates": (
                    _serialize_bricklink_candidates(candidates)
                ),
            })

    part_map_df = pd.DataFrame(resolved_rows, columns=part_map_columns)

    if use_cache:
        _merge_into_part_map(part_map_df)

    return part_map_df


# =============================================================================
# Add bricklink_part_num and bricklink_color_id columns to a parts dataframe.
#
# Used on the official set inventory so BrickLink values are available for
# display and for the Wanted List export without changing the Rebrickable
# keys the comparison joins on.
# =============================================================================
def attach_bricklink_ids(parts_df, color_map_df=None):
    result_df = parts_df.copy()

    if result_df.empty:
        result_df["bricklink_part_num"] = ""
        result_df["bricklink_part_candidates"] = ""
        result_df["bricklink_color_id"] = pd.Series(dtype="Int64")
        return result_df

    if color_map_df is None:
        color_map_df = load_color_map()

    result_df["bricklink_color_id"] = map_rebrickable_color_ids(
        result_df["color_id"],
        color_map_df,
    ).values

    part_map_df = resolve_rebrickable_part_nums(result_df["part_num"])
    part_lookup = dict(
        zip(
            part_map_df["rebrickable_part_num"],
            part_map_df["bricklink_part_num"],
        )
    )
    candidate_lookup = dict(
        zip(
            part_map_df["rebrickable_part_num"],
            part_map_df["bricklink_part_candidates"],
        )
    )

    result_df["bricklink_part_num"] = [
        part_lookup.get(str(part_num), str(part_num))
        for part_num in result_df["part_num"]
    ]
    result_df["bricklink_part_candidates"] = [
        candidate_lookup.get(str(part_num), "")
        for part_num in result_df["part_num"]
    ]

    return result_df


# =============================================================================
# Fill blank BrickLink columns using cached data only.
#
# Comparison results include rows that came from the imported CSV rather than
# the official inventory, so they never went through attach_bricklink_ids.
# This runs with no API calls, which keeps the compare step off the network.
# =============================================================================
def fill_missing_bricklink_ids(df, color_map_df=None):
    result_df = df.copy()

    if result_df.empty:
        for column, dtype in [
            ("bricklink_part_num", "object"),
            ("bricklink_color_id", "Int64"),
        ]:
            if column not in result_df.columns:
                result_df[column] = pd.Series(dtype=dtype)
        return result_df

    if color_map_df is None:
        color_map_df = load_color_map()

    if "bricklink_color_id" not in result_df.columns:
        result_df["bricklink_color_id"] = pd.NA

    result_df["bricklink_color_id"] = pd.to_numeric(
        result_df["bricklink_color_id"],
        errors="coerce",
    ).astype("Int64")

    translated_colors = map_rebrickable_color_ids(
        result_df["color_id"],
        color_map_df,
    )
    result_df["bricklink_color_id"] = (
        result_df["bricklink_color_id"]
        .fillna(pd.Series(translated_colors.values, index=result_df.index))
    )

    if "bricklink_part_num" not in result_df.columns:
        result_df["bricklink_part_num"] = ""

    cached_part_map = load_part_map()
    part_lookup = {
        row["rebrickable_part_num"]: row["bricklink_part_num"]
        for _, row in cached_part_map.iterrows()
        if row["rebrickable_part_num"] and row["bricklink_part_num"]
    }

    current_part_nums = (
        result_df["bricklink_part_num"].astype("string").fillna("").str.strip()
    )

    result_df["bricklink_part_num"] = [
        existing
        if existing
        else part_lookup.get(str(part_num), str(part_num))
        for existing, part_num in zip(
            current_part_nums.tolist(),
            result_df["part_num"].tolist(),
        )
    ]

    return result_df
