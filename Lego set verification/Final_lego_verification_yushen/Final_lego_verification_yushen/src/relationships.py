'''
Mold-variations, alternatives, counterparts for parts in a set.

  - attach_variation_relationships() uses local CSV file to show "this part has variations" 
  on the full parts table.
  - list_parts_with_variations() uses local CSV file to filter the full parts table 
    down to just the parts that have at least one mold variation or alternative.
  - attach_variation_color_details() add color availability details to mold-variation and alternative rows.
  - get_selected_variation_details() hits the API for ONE part at a time,
    used only when the user clicks a part to see the full details.
  - get_all_variation_details() hits the API for all variation parts at once, 
  used when the user wants to see all the details of all variation parts in a set.
'''

import gzip
from io import BytesIO
import os
from pathlib import Path
import shutil
import tempfile

import pandas as pd
import requests
from src.api_rebrickable import (get_parts_bulk_df, get_part_colors_df)
from config import relationships_csv_path, relationships_download_url
from typing import Optional

# Define relationship categories 
variation_rel_types = ("M", "A")
counterpart_rel_types = ("P", "R", "B")
all_rel_types = variation_rel_types + counterpart_rel_types

# Report statuses that mean "on hand but not counted toward the set".
surplus_statuses = ("Not in set", "Extra")

rel_type_names = {"M": "mold variation", "A": "alternative"}

relationship_columns = [
    "rel_type",
    "child_part_num",
    "parent_part_num",
]

# Load local CSV file
def load_relationships(csv_path=relationships_csv_path):
    return pd.read_csv(csv_path, dtype=str)


def _validated_relationships(dataframe, previous_row_count=0):
    """Return a normalized download or reject a suspicious replacement."""
    if list(dataframe.columns) != relationship_columns:
        raise ValueError(
            "Downloaded relationship data has unexpected columns: "
            f"{', '.join(str(column) for column in dataframe.columns)}"
        )

    result_df = dataframe[relationship_columns].copy()
    for column in relationship_columns:
        result_df[column] = (
            result_df[column].astype("string").fillna("").str.strip()
        )
        if (result_df[column] == "").any():
            raise ValueError(
                f"Downloaded relationship data has blank {column} values."
            )

    if len(result_df) < 1000:
        raise ValueError(
            "Downloaded relationship data is unexpectedly small "
            f"({len(result_df)} rows)."
        )

    if previous_row_count and len(result_df) < previous_row_count * 0.8:
        raise ValueError(
            "Downloaded relationship data lost more than 20% of the "
            f"existing rows ({previous_row_count} to {len(result_df)})."
        )

    return result_df


def refresh_relationships_csv(
    csv_path=relationships_csv_path,
    download_url=relationships_download_url,
):
    """Safely replace the local relationship table from Rebrickable."""
    target_path = Path(csv_path)
    previous_row_count = 0
    if target_path.exists():
        previous_row_count = len(load_relationships(target_path))

    response = requests.get(download_url, timeout=30)
    response.raise_for_status()

    try:
        csv_bytes = gzip.decompress(response.content)
    except (OSError, EOFError) as exc:
        raise ValueError(
            "Rebrickable returned an invalid compressed relationship file."
        ) from exc

    try:
        downloaded_df = pd.read_csv(BytesIO(csv_bytes), dtype=str)
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise ValueError(
            "Rebrickable returned an unreadable relationship CSV."
        ) from exc

    validated_df = _validated_relationships(
        downloaded_df,
        previous_row_count=previous_row_count,
    )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            suffix=".tmp",
            dir=target_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            validated_df.to_csv(temporary_file, index=False)

        if target_path.exists():
            backup_path = target_path.with_suffix(
                f"{target_path.suffix}.bak"
            )
            shutil.copy2(target_path, backup_path)

        os.replace(temporary_path, target_path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    return validated_df.reset_index(drop=True)

# =========================== Variations ======================================
# Build a lookup table where each part_num has related mold variations 
# or alternatives.rel_types: "M" = mold variation and "A" = alternate
# =============================================================================
def build_variation_lookup(relationships_df):
    rel_types = variation_rel_types
    rel = relationships_df[relationships_df["rel_type"].isin(rel_types)].copy()

    # If the set part is the child, show the parent as related.
    child_to_parent = rel.rename(columns={
        "child_part_num": "part_num",
        "parent_part_num": "related_part_num"}).copy()
    child_to_parent["relationship_direction"] = "child_to_parent"

    # If the set part is the parent, show the child as related.
    parent_to_child = rel.rename(columns={
        "parent_part_num": "part_num",
        "child_part_num": "related_part_num"}).copy()
    parent_to_child["relationship_direction"] = "parent_to_child"

    relationship_long_df = pd.concat(
        [child_to_parent, parent_to_child], ignore_index=True)

    relationship_long_df = relationship_long_df[
        ["part_num", "rel_type", "related_part_num", "relationship_direction"]
    ].dropna().drop_duplicates()

    relationship_long_df["part_num"] = relationship_long_df["part_num"].astype(str)
    relationship_long_df["related_part_num"] = relationship_long_df["related_part_num"].astype(str)

    return relationship_long_df

# =============================================================================
# Collapse related part numbers into display-friendly columns
# =============================================================================
def build_variation_summary(relationship_long_df):
    summary = (
        relationship_long_df
        .groupby(["part_num", "rel_type"])["related_part_num"]
        .apply(lambda x: ", ".join(sorted(x.astype(str).unique())))
        .unstack(fill_value="")
        .reset_index())

    summary = summary.rename(columns={"M": "mold_variations", "A": "alternatives"})

    # Make sure both columns exist even if no rows of that type exist
    for col in ["mold_variations", "alternatives"]:
        if col not in summary.columns:
            summary[col] = ""

    return summary[["part_num", "mold_variations", "alternatives"]]


# =============================================================================
# Add mold_variations and alternatives columns to a parts dataframe.
# Local CSV lookup only. No API calls, safe to run on the full set.
# =============================================================================
def attach_variation_relationships(parts_df, relationships_df):
    parts_df = parts_df.copy()
    parts_df["part_num"] = parts_df["part_num"].astype(str)

    relationship_long_df = build_variation_lookup(relationships_df)
    summary_df = build_variation_summary(relationship_long_df)

    parts_display_df = parts_df.merge(summary_df, on="part_num", how="left")
    parts_display_df["mold_variations"] = parts_display_df["mold_variations"].fillna("")
    parts_display_df["alternatives"] = parts_display_df["alternatives"].fillna("")

    return parts_display_df

# =============================================================================
# Filter a parts table down to just the rows that have at least one mold variation 
# or alternative. Local-only, no API call.
# To help a user decide which parts are worth checking with get_related_parts_details()
# =============================================================================
def list_parts_with_variations(parts_display_df):
    has_relations = (
        (parts_display_df["mold_variations"] != "") |
        (parts_display_df["alternatives"] != ""))
    return parts_display_df[has_relations].copy()

# =============================================================================
# Add color availability details to mold-variation and alternative rows.
#
# For each related part:
#   - Fetch the colors in which the related part appears.
#   - Check whether it exists in the selected required-part color.
#   - List its available color IDs and names.
#   - Use the selected-color image when one is available.
#
# This function enriches relationship display data only. It does not change
# required quantities or automatically treat a related part as a replacement.
# =============================================================================

def attach_variation_color_details(variation_details_df, selected_color_id,):
    """Add color availability and color-specific images to related parts."""
    result_df = variation_details_df.copy()

    if result_df.empty:
        return result_df

    if selected_color_id is None or pd.isna(selected_color_id):
        result_df["selected_color_id"] = pd.NA
        result_df["same_color_available"] = pd.NA
        result_df["available_color_ids"] = ""
        result_df["available_color_names"] = ""
        return result_df

    selected_color_id = int(selected_color_id)
    enriched_rows = []

    for _, row in result_df.iterrows():
        related_part_num = str(
            row["related_part_num"]
        )

        colors_df = get_part_colors_df(
            related_part_num
        )

        enriched_row = row.to_dict()
        enriched_row["selected_color_id"] = selected_color_id

        if colors_df.empty:
            enriched_row["same_color_available"] = False
            enriched_row["available_color_ids"] = ""
            enriched_row["available_color_names"] = ""

            # Keep the general part image already returned by the bulk lookup.
            enriched_rows.append(enriched_row)
            continue

        available_color_ids = (
            colors_df["color_id"]
            .dropna()
            .astype(int)
            .drop_duplicates()
            .tolist()
        )

        available_color_names = (
            colors_df["color_name"]
            .dropna()
            .astype(str)
            .drop_duplicates()
            .tolist()
        )

        same_color_rows = colors_df[
            colors_df["color_id"] == selected_color_id
        ]

        same_color_available = not same_color_rows.empty

        enriched_row["same_color_available"] = (
            same_color_available
        )
        enriched_row["available_color_ids"] = ", ".join(
            str(color_id)
            for color_id in available_color_ids
        )
        enriched_row["available_color_names"] = ", ".join(
            available_color_names
        )

        # Prefer an image showing the related part in the selected color.
        if same_color_available:
            matching_image_url = (
                same_color_rows.iloc[0]
                .get("image_url", "")
            )

            if (
                pd.notna(matching_image_url)
                and str(matching_image_url).strip()
            ):
                enriched_row["related_image_url"] = (
                    str(matching_image_url).strip()
                )

        enriched_rows.append(enriched_row)

    return pd.DataFrame(enriched_rows)

# =============================================================================
# For ONE part, fetch full details (name, image) of its mold variations and 
# alternatives. Called on demand when the user wants to see the details of a part. 
# This is the first function in this module that hits the API.
# =============================================================================
def get_selected_variation_details(part_num, relationships_df, selected_color_id=None):
    relationship_long_df = build_variation_lookup(relationships_df)

    part_relationships = relationship_long_df[
        relationship_long_df["part_num"] == str(part_num)]

    if part_relationships.empty:
        return pd.DataFrame()

    related_part_nums = part_relationships["related_part_num"].unique().tolist()
    details_df = get_parts_bulk_df(related_part_nums, inc_part_details=True)

    if details_df.empty:
        return pd.DataFrame()

    details_df = details_df.rename(columns={
        "part_num": "related_part_num",
        "part_name": "related_part_name",
        "part_category_id": "related_part_category_id",
        "part_url": "related_part_url",
        "image_url": "related_image_url"})

    result_df = part_relationships.merge(
        details_df,
        on="related_part_num",
        how="left",
    )

    return attach_variation_color_details(
        result_df,
        selected_color_id,
    )

# =============================================================================
# Fetch details for all the related mold variation / alternative parts.
#
# When selected_color_id is provided, also check whether each related part
# exists in that color and prefer a color-specific image when available.
# Called on demand when the user selects a part and requests relationship
# details. This function makes API requests.
# =============================================================================
def get_all_variation_details(required_parts_df, relationships_df):
    relationship_long_df = build_variation_lookup(relationships_df)
    
    # Get part numbers from this set only
    set_part_nums = required_parts_df["part_num"].dropna().astype(str).unique().tolist()
    
    # Keep only relationships for parts in this set
    relevant_relationships_df = relationship_long_df[
        relationship_long_df["part_num"].isin(set_part_nums)].copy()
    
    # Get only the related variation/alternative part numbers for this set
    related_part_nums = (
        relevant_relationships_df["related_part_num"]
        .dropna()
        .astype(str)
        .unique()
        .tolist())
 
    if not related_part_nums:
        return pd.DataFrame()
 
    related_parts_details_df = get_parts_bulk_df(related_part_nums, inc_part_details=True)
 
    if related_parts_details_df.empty:
        return pd.DataFrame()
 
    # rename columns for joining to relationship data
    related_parts_details_df = related_parts_details_df.rename(columns={
        "part_num": "related_part_num",
        "part_name": "related_part_name",
        "part_category_id": "related_part_category_id",
        "part_url": "related_part_url",
        "image_url": "related_image_url"})
    
    # Join details back to the relationship table
    return relevant_relationships_df.merge(related_parts_details_df, on="related_part_num", how="left")

# =============================================================================
# Find imported parts that are mold variations or alternatives of a part the
# set actually needs.
#
# An imported part qualifies only when it is not itself required by the set in
# that color, but is related to a part that is needed in the same color. The
# result is a proposal for the user to accept or reject; nothing is substituted
# automatically.
#
# A variation in a color the set does not need cannot fill the slot, so it is
# deliberately not proposed. compare_parts leaves it as a surplus row and
# attach_variation_surplus_notes explains it, which needs no decision from the
# user.
#
# Local CSV lookup only. No API calls.
# =============================================================================
def find_variation_substitutions(have_df, required_parts_df, relationships_df):
    columns = [
        "csv_part_num",
        "csv_color_id",
        "quantity_have",
        "set_part_num",
        "set_part_name",
        "set_color_id",
        "set_color_name",
        "quantity_required",
        "rel_type",
    ]

    if have_df is None or have_df.empty or required_parts_df.empty:
        return pd.DataFrame(columns=columns)

    required = required_parts_df.copy()
    required["part_num"] = required["part_num"].astype(str)
    required["color_id"] = pd.to_numeric(
        required["color_id"], errors="coerce").astype("Int64")
    if "quantity_required" not in required.columns:
        required["quantity_required"] = 0
    required["quantity_required"] = pd.to_numeric(
        required["quantity_required"], errors="coerce").fillna(0).astype(int)

    set_part_nums = set(required["part_num"])
    set_keys = set(
        zip(required["part_num"], required["color_id"]))

    # One name/color label per required part/color for display.
    required_details = (
        required.drop_duplicates(subset=["part_num", "color_id"])
        .set_index(["part_num", "color_id"])[
            ["part_name", "color_name", "quantity_required"]
        ]
        .to_dict(orient="index"))

    relationship_long_df = build_variation_lookup(relationships_df)

    # related_part_num is the imported part; part_num is the set-side candidate.
    variation_lookup = {}
    for _, row in relationship_long_df.iterrows():
        variation_lookup.setdefault(
            row["related_part_num"], []).append(
                (row["part_num"], row["rel_type"]))

    have = have_df.copy()
    have["part_num"] = have["part_num"].astype(str)
    have["color_id"] = pd.to_numeric(
        have["color_id"], errors="coerce").astype("Int64")

    proposals = []

    for _, row in have.iterrows():
        csv_part_num = row["part_num"]
        csv_color_id = row["color_id"]
        quantity = int(row.get("quantity_have", 0) or 0)

        if quantity <= 0:
            continue

        # Already an official part of this set in this color, nothing to offer.
        if (csv_part_num, csv_color_id) in set_keys:
            continue

        candidates = variation_lookup.get(csv_part_num, [])

        for set_part_num, rel_type in candidates:
            if set_part_num not in set_part_nums:
                continue

            # The set must need this part in the color actually on hand.
            if (set_part_num, csv_color_id) not in set_keys:
                continue

            set_color_id = csv_color_id

            details = required_details.get(
                (set_part_num, set_color_id), {})

            proposals.append({
                "csv_part_num": csv_part_num,
                "csv_color_id": csv_color_id,
                "quantity_have": quantity,
                "set_part_num": set_part_num,
                "set_part_name": details.get("part_name", ""),
                "set_color_id": set_color_id,
                "set_color_name": details.get("color_name", ""),
                "quantity_required": int(
                    details.get("quantity_required", 0) or 0),
                "rel_type": rel_type,
            })

    if not proposals:
        return pd.DataFrame(columns=columns)

    proposals_df = pd.DataFrame(proposals, columns=columns)

    # Keep one row per imported part and substitution target.
    return (
        proposals_df
        .sort_values(
            by=["csv_part_num", "set_part_num"],
            kind="stable")
        .drop_duplicates(
            subset=["csv_part_num", "csv_color_id", "set_part_num"],
            keep="first")
        .reset_index(drop=True))


# =============================================================================
# Allocate accepted substitution quantities onto the set parts they stand in
# for.
#
# Several accepted rows may draw from the same imported part/color pool. Each
# row therefore carries quantity_allocated; their sum may not exceed the source
# quantity. Any unallocated quantity remains on the imported source row.
# =============================================================================
def apply_variation_substitutions(
        have_df,
        accepted_df,
        target_shortages=None):
    if accepted_df is None or accepted_df.empty:
        return have_df.copy()

    def color_key(value):
        if value is None or pd.isna(value):
            return None
        return int(value)

    def whole_number(value, label):
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.isna(numeric) or float(numeric) != int(numeric):
            raise ValueError(f"{label} must be a whole number.")
        numeric = int(numeric)
        if numeric < 0:
            raise ValueError(f"{label} cannot be negative.")
        return numeric

    quantity_by_key = {}
    key_order = []
    normalized_have = have_df.copy()
    normalized_have["part_num"] = normalized_have["part_num"].astype(str)
    normalized_have["color_id"] = pd.to_numeric(
        normalized_have["color_id"], errors="coerce").astype("Int64")
    if "bricklink_color_id" not in normalized_have.columns:
        normalized_have["bricklink_color_id"] = pd.NA
    normalized_have["bricklink_color_id"] = pd.to_numeric(
        normalized_have["bricklink_color_id"], errors="coerce"
    ).astype("Int64")
    normalized_have["quantity_have"] = pd.to_numeric(
        normalized_have["quantity_have"], errors="coerce").fillna(0).astype(int)

    for _, row in normalized_have.iterrows():
        key = (
            str(row["part_num"]),
            color_key(row["color_id"]),
            color_key(row["bricklink_color_id"]),
        )
        if key not in quantity_by_key:
            quantity_by_key[key] = 0
            key_order.append(key)
        quantity_by_key[key] += int(row["quantity_have"])

    allocations = []
    source_totals = {}
    target_totals = {}
    allocation_column = (
        "quantity_allocated"
        if "quantity_allocated" in accepted_df.columns
        else "quantity_have"
    )

    for _, row in accepted_df.iterrows():
        quantity = whole_number(
            row.get(allocation_column, 0),
            "Allocated quantity",
        )
        if quantity == 0:
            continue

        source_key = (
            str(row["csv_part_num"]),
            color_key(row["csv_color_id"]),
            None,
        )
        target_key = (
            str(row["set_part_num"]),
            color_key(row["set_color_id"]),
            None,
        )
        allocations.append((source_key, target_key, quantity))
        source_totals[source_key] = source_totals.get(source_key, 0) + quantity
        target_totals[target_key] = target_totals.get(target_key, 0) + quantity

    if not allocations:
        return normalized_have.reset_index(drop=True)

    for source_key, allocated in source_totals.items():
        available = quantity_by_key.get(source_key)
        if available is None:
            raise ValueError(
                "Variation source "
                f"{source_key[0]} / color {source_key[1]} is not on hand."
            )
        if allocated > available:
            raise ValueError(
                f"Allocated {allocated} from {source_key[0]} / color "
                f"{source_key[1]}, but only {available} is available."
            )

    if target_shortages is not None:
        normalized_shortages = {
            (str(part_num), color_key(color_id)): whole_number(
                shortage,
                "Target shortage",
            )
            for (part_num, color_id), shortage in target_shortages.items()
        }
        for target_key, allocated in target_totals.items():
            shortage = normalized_shortages.get(target_key[:2], 0)
            if allocated > shortage:
                raise ValueError(
                    f"Allocated {allocated} to {target_key[0]} / color "
                    f"{target_key[1]}, but its remaining shortage is "
                    f"{shortage}."
                )

    # Every validation happens before quantities change, so a rejected
    # allocation cannot leave a half-applied import behind.
    for source_key, allocated in source_totals.items():
        quantity_by_key[source_key] -= allocated

    for target_key, allocated in target_totals.items():
        if target_key not in quantity_by_key:
            quantity_by_key[target_key] = 0
            key_order.append(target_key)
        quantity_by_key[target_key] += allocated

    result_rows = []
    allocated_sources = set(source_totals)
    for part_num, color_id, bricklink_color_id in key_order:
        inventory_key = (part_num, color_id, bricklink_color_id)
        quantity = quantity_by_key[inventory_key]
        if quantity == 0 and inventory_key in allocated_sources:
            continue
        result_rows.append({
            "part_num": part_num,
            "color_id": pd.NA if color_id is None else color_id,
            "bricklink_color_id": (
                pd.NA
                if bricklink_color_id is None
                else bricklink_color_id
            ),
            "quantity_have": quantity,
        })

    result_df = pd.DataFrame(
        result_rows,
        columns=[
            "part_num",
            "color_id",
            "bricklink_color_id",
            "quantity_have",
        ],
    )
    result_df["color_id"] = pd.to_numeric(
        result_df["color_id"], errors="coerce").astype("Int64")
    result_df["bricklink_color_id"] = pd.to_numeric(
        result_df["bricklink_color_id"], errors="coerce"
    ).astype("Int64")
    return result_df.reset_index(drop=True)


# =============================================================================
# Explain surplus report rows that are variations of parts the set needs.
#
# A variation shows up as surplus for one of two reasons: it is in a color the
# set does not need, or the user skipped the substitution. Either way the row
# reads as an unrelated extra, so name the set part it relates to.
#
# This is the only explanation a surplus row gets, and it works across part
# numbers, so a variation pair such as 50990b against 50990a is still named.
#
# Local CSV lookup only. No API calls.
# =============================================================================
def attach_variation_surplus_notes(
        report_df, required_parts_df, relationships_df):
    result_df = report_df.copy()

    if "variation_note" not in result_df.columns:
        result_df["variation_note"] = ""

    result_df["variation_note"] = (
        result_df["variation_note"].astype("object").fillna(""))

    if result_df.empty or required_parts_df.empty:
        return result_df

    required = required_parts_df.copy()
    required["part_num"] = required["part_num"].astype(str)
    required["color_id"] = pd.to_numeric(
        required["color_id"], errors="coerce").astype("Int64")

    set_part_nums = set(required["part_num"])

    part_names = (
        required.drop_duplicates(subset=["part_num"])
        .set_index("part_num")["part_name"]
        .to_dict())

    # Every color the set needs each part in, so the note can say which.
    needed_colors = {}
    for _, row in required.iterrows():
        needed_colors.setdefault(row["part_num"], {})[
            row["color_id"]] = row["color_name"]

    variation_lookup = {}
    for _, row in build_variation_lookup(relationships_df).iterrows():
        variation_lookup.setdefault(
            row["related_part_num"], set()).add(
                (row["part_num"], row["rel_type"]))

    notes = []

    for _, row in result_df.iterrows():
        existing_note = row.get("variation_note") or ""

        if row.get("status") not in surplus_statuses:
            notes.append(existing_note)
            continue

        surplus_part_num = str(row.get("part_num", ""))
        surplus_color_id = pd.to_numeric(
            row.get("color_id"), errors="coerce")

        if pd.notna(surplus_color_id):
            surplus_color_id = int(surplus_color_id)

        candidates = sorted(
            pair
            for pair in variation_lookup.get(surplus_part_num, set())
            if pair[0] in set_part_nums)

        row_notes = []

        for set_part_num, rel_type in candidates:
            colors = needed_colors.get(set_part_num, {})
            label = rel_type_names.get(rel_type, "variation")
            set_part_name = part_names.get(set_part_num, "")
            described = (
                f"{set_part_num} ({set_part_name})"
                if set_part_name
                else set_part_num)

            if surplus_color_id in colors:
                # Same color, so the user was offered this and skipped it.
                row_notes.append(
                    f"Skipped {label} of {described}")
            else:
                wanted = ", ".join(
                    str(name)
                    for _, name in sorted(
                        colors.items(),
                        key=lambda item: (
                            item[0] is pd.NA, str(item[1])))
                    if name)
                row_notes.append(
                    f"{label.capitalize()} of {described}, which this set "
                    f"needs in {wanted}" if wanted
                    else f"{label.capitalize()} of {described}")

        combined = "; ".join(row_notes)

        if existing_note and combined:
            notes.append(f"{existing_note}; {combined}")
        else:
            notes.append(existing_note or combined)

    result_df["variation_note"] = notes

    return result_df


# ========================== Counterparts =====================================
# Filter the relationships CSV down to P, R, B relationships, that are 
# relevant to the LEGO set.
# Local CSV lookup only. This function does not call the API.
# =============================================================================
def _counterpart_color_key(value):
    """Normalize a Rebrickable color ID for relationship grouping."""
    numeric_value = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric_value):
        return "none"
    return str(int(numeric_value))


def get_relevant_counterpart(required_parts_df, relationships_df):
    set_part_nums = set(required_parts_df["part_num"].dropna().astype(str))

    counterpart_df = relationships_df[
        relationships_df["rel_type"].isin(counterpart_rel_types)].copy()

    counterpart_df = counterpart_df.dropna(subset=["parent_part_num", "child_part_num"])

    counterpart_df["parent_part_num"] = (counterpart_df["parent_part_num"].astype(str))
    counterpart_df["child_part_num"] = (counterpart_df["child_part_num"].astype(str))

    counterpart_df["parent_in_set"] = (counterpart_df["parent_part_num"].isin(set_part_nums))
    counterpart_df["child_in_set"] = (counterpart_df["child_part_num"].isin(set_part_nums))

    # A catalog relationship becomes a set counterpart only when both ends of
    # that specific P/R/B edge are official inventory parts. This is pairwise:
    # an assembly may keep one present child without requiring every catalog
    # child, while an assembly or child by itself remains a normal set part.
    both_members_in_set = (
        counterpart_df["parent_in_set"]
        & counterpart_df["child_in_set"]
    )
    relevant_df = counterpart_df[both_members_in_set].copy()

    # A printed part corresponds only to a plain base in the same color. Part
    # numbers alone are insufficient when a set contains that base in several
    # colors. R pairs and B assembly/subpart relationships are not forced to
    # share a color because those physical counterparts may legitimately differ.
    if "color_id" in required_parts_df.columns and not relevant_df.empty:
        required_colors = required_parts_df[["part_num", "color_id"]].copy()
        required_colors["part_num"] = required_colors["part_num"].astype(str)
        required_colors["_counterpart_color_key"] = required_colors[
            "color_id"
        ].apply(_counterpart_color_key)
        colors_by_part = (
            required_colors.groupby("part_num")["_counterpart_color_key"]
            .agg(set)
            .to_dict()
        )

        print_mask = relevant_df["rel_type"] == "P"
        print_color_matches = relevant_df.apply(
            lambda row: bool(
                colors_by_part.get(row["parent_part_num"], set())
                & colors_by_part.get(row["child_part_num"], set())
            ),
            axis=1,
        )
        relevant_df = relevant_df[~print_mask | print_color_matches].copy()

    return relevant_df.drop_duplicates().reset_index(drop=True)

# =============================================================================
# Create a stable ID for the synthetic counterpart group shown in the UI.
#
# This group ID is not a LEGO part number and is not the same as the CSV parent.
# It represents the expandable summary row that contains related member rows.
# =============================================================================
def create_counterpart_group_id(row):
    rel_type = row["rel_type"]
    csv_parent = row["parent_part_num"]
    csv_child = row["child_part_num"]

    if rel_type == "P":
        return f"P:{csv_child}"

    if rel_type == "R":
        pair_members = sorted([csv_parent, csv_child])
        return f"R:{pair_members[0]}:{pair_members[1]}"

    if rel_type == "B":
        return f"B:{csv_parent}"

    raise ValueError(
        f"Unsupported counterpart relationship type: {rel_type}"
    )

# =============================================================================
# Give a counterpart a user-friendly role based on its relationship
# type and whether it came from the CSV parent or child column.
#
# These roles describe the source relationship. They do not determine whether
# the part is officially required or whether the group is complete.
# =============================================================================
def get_counterpart_role(rel_type, csv_side):
    if rel_type == "P":
        if csv_side == "parent":
            return "plain_base"

        return "printed_part"

    if rel_type == "R":
        return "pair_member"

    if rel_type == "B":
        if csv_side == "parent":
            return "assembly"

        return "subpart"

    return "counterpart"

# =============================================================================
# Convert relevant counterpart relationships into one row per group member.
# =============================================================================
def build_counterpart_table(required_parts_df, relevant_counterparts_df):
    if relevant_counterparts_df.empty:
        return pd.DataFrame()

    relationships = relevant_counterparts_df.copy()

    relationships["counterpart_group_id"] = relationships.apply(create_counterpart_group_id, axis=1)

    parent_members = relationships[
        ["counterpart_group_id", "rel_type", "parent_part_num"]].rename(columns={"parent_part_num": "member_part_num"})

    parent_members["csv_side"] = "parent"

    child_members = relationships[
        ["counterpart_group_id", "rel_type", "child_part_num"]].rename(columns={"child_part_num": "member_part_num"})

    child_members["csv_side"] = "child"

    counterpart_members_df = pd.concat([parent_members, child_members],ignore_index=True)

    counterpart_members_df["member_part_num"] = (counterpart_members_df["member_part_num"].astype(str))

    counterpart_members_df["member_role"] = (
        counterpart_members_df.apply(lambda row: get_counterpart_role(
                row["rel_type"], row["csv_side"]),axis=1))

    
    counterpart_members_df = counterpart_members_df.drop_duplicates(
        subset=["counterpart_group_id", "member_part_num", "member_role"])

    set_part_nums = set(required_parts_df["part_num"].dropna().astype(str))
   
    counterpart_members_df["is_required_in_set"] = (
        counterpart_members_df["member_part_num"].isin(set_part_nums))

    # Only official set inventory rows belong in the verification table.
    counterpart_members_df = counterpart_members_df[
        counterpart_members_df["is_required_in_set"]
    ].copy()

    if counterpart_members_df.empty:
        return pd.DataFrame()

    # Reuse names, images, colors, quantities, and other existing set details.
    set_parts_df = required_parts_df.copy()
    set_parts_df["part_num"] = set_parts_df["part_num"].astype(str)
    if "color_id" in set_parts_df.columns:
        set_parts_df["_counterpart_color_key"] = set_parts_df[
            "color_id"
        ].apply(_counterpart_color_key)
    else:
        set_parts_df["_counterpart_color_key"] = "none"

    set_parts_df = set_parts_df.rename(columns={"part_num": "member_part_num"})

    counterpart_members_df = counterpart_members_df.merge(
        set_parts_df,
        on="member_part_num",
        how="left",
        suffixes=("", "_set"),
    )

    # Retain only same-color plain/printed member pairs, then make their group
    # IDs color-specific so another color of the same base stays independent.
    print_mask = counterpart_members_df["rel_type"] == "P"
    if print_mask.any():
        print_role_counts = (
            counterpart_members_df.loc[print_mask]
            .groupby([
                "counterpart_group_id",
                "_counterpart_color_key",
            ])["member_role"]
            .nunique()
        )
        valid_print_keys = set(
            print_role_counts[print_role_counts >= 2].index
        )
        row_print_keys = list(zip(
            counterpart_members_df["counterpart_group_id"],
            counterpart_members_df["_counterpart_color_key"],
        ))
        valid_print_mask = pd.Series(
            [key in valid_print_keys for key in row_print_keys],
            index=counterpart_members_df.index,
        )
        counterpart_members_df = counterpart_members_df[
            ~print_mask | valid_print_mask
        ].copy()
        print_mask = counterpart_members_df["rel_type"] == "P"
        counterpart_members_df.loc[
            print_mask,
            "counterpart_group_id",
        ] = (
            counterpart_members_df.loc[print_mask, "counterpart_group_id"]
            + ":C:"
            + counterpart_members_df.loc[
                print_mask,
                "_counterpart_color_key",
            ]
        )

    return counterpart_members_df

# =============================================================================
# Build all counterpart groups and their member rows for the current set.
# This is the main counterpart function that app.py can call.
# Local processing only. No API calls.
# =============================================================================
def build_counterpart_groups(required_parts_df, relationships_df):
    relevant_counterparts_df = (
        get_relevant_counterpart(required_parts_df, relationships_df))

    return build_counterpart_table(required_parts_df, relevant_counterparts_df)

# =============================================================================
#Return the portion of a part number before 'pr'.
#
#    Examples:
#        '333pr444' -> '333'
#        '333' -> None
#        'pr444' -> None
# =============================================================================
def extract_pr_base_part_num(part_num: object) -> Optional[str]:
    if pd.isna(part_num):
        return None

    value = str(part_num).strip()
    lowercase_value = value.lower()

    marker_position = lowercase_value.find("pr")

    # No "pr", or "pr" appears at the beginning.
    if marker_position <= 0:
        return None

    base_part_num = value[:marker_position].strip()

    return base_part_num or None

# =============================================================================
# Mark 'pr' parts as display subrows when their base part is also present in the 
# official required-parts dataframe.
#
# This function does not remove rows, merge quantities, or change comparison keys.
# =============================================================================
def attach_pr_subrow_metadata(parts_df: pd.DataFrame) -> pd.DataFrame:
    
    result_df = parts_df.copy()

    normalized_part_nums = (result_df["part_num"].astype("string").str.strip()    )

    required_part_nums = set(normalized_part_nums.dropna().tolist())

    result_df["pr_base_part_num"] = normalized_part_nums.map(
        extract_pr_base_part_num
    )

    # A potential base becomes a real parent only when that part number
    # exists in this set's official required inventory.
    result_df["pr_parent_part_num"] = (
        result_df["pr_base_part_num"]
        .where(
            result_df["pr_base_part_num"].isin(required_part_nums)
        )
    )

    result_df["is_pr_subrow"] = (
        result_df["pr_parent_part_num"].notna()
    )

    result_df["pr_display_level"] = (
        result_df["is_pr_subrow"].astype("int8")
    )

    return result_df

# =============================================================================
# Identify display-only families of plain and printed parts.
#
# A P relationship links:
#   parent_part_num = plain base part
#   child_part_num = printed/decorated part
#
# Parts are grouped only when at least two different official inventory parts
# share the same base part and color. For example, plain 333 and printed
# 333pr444 in blue can form one display family.
#
# This function adds metadata only. It never changes quantity_required and
# never adds synthetic rows to the official inventory.
# =============================================================================
def attach_print_family_metadata(parts_df, relationships_df):
    result_df = parts_df.copy()

    result_df["part_num"] = (
        result_df["part_num"]
        .fillna("")
        .astype(str)
    )

    result_df["color_id"] = pd.to_numeric(
        result_df["color_id"],
        errors="coerce",
    ).astype("Int64")

    # Consistent defaults for rows that do not belong to a print family.
    result_df["print_family_id"] = ""
    result_df["print_family_base_part_num"] = ""
    result_df["print_family_role"] = ""
    result_df["has_print_family"] = False

    if result_df.empty or relationships_df.empty:
        return result_df

    print_relationships_df = relationships_df[
        relationships_df["rel_type"] == "P"
    ][
        [
            "parent_part_num",
            "child_part_num",
        ]
    ].dropna().drop_duplicates().copy()

    if print_relationships_df.empty:
        return result_df

    print_relationships_df["parent_part_num"] = (
        print_relationships_df["parent_part_num"]
        .astype(str)
    )
    print_relationships_df["child_part_num"] = (
        print_relationships_df["child_part_num"]
        .astype(str)
    )

    required_part_nums = set(
        result_df["part_num"]
    )

    # A print relationship is relevant when its printed child is officially
    # required by this set.
    relevant_prints_df = print_relationships_df[
        print_relationships_df["child_part_num"].isin(
            required_part_nums
        )
    ].copy()

    if relevant_prints_df.empty:
        return result_df

    # Use one deterministic base if duplicate relationship rows exist.
    child_to_base = (
        relevant_prints_df
        .sort_values(
            by=[
                "child_part_num",
                "parent_part_num",
            ],
            kind="stable",
        )
        .drop_duplicates(
            subset=["child_part_num"],
            keep="first",
        )
        .set_index("child_part_num")["parent_part_num"]
        .to_dict()
    )

    relevant_base_parts = set(
        relevant_prints_df["parent_part_num"]
    )

    # Printed parts map to their plain base.
    result_df["_print_base"] = (
        result_df["part_num"]
        .map(child_to_base)
    )

    # An officially required plain part maps to itself as the base.
    plain_base_mask = result_df["part_num"].isin(
        relevant_base_parts
    )

    result_df.loc[
        plain_base_mask,
        "_print_base",
    ] = result_df.loc[
        plain_base_mask,
        "part_num",
    ]

    candidate_mask = result_df["_print_base"].notna()

    if not candidate_mask.any():
        return result_df.drop(columns=["_print_base"])

    result_df.loc[
        candidate_mask,
        "print_family_base_part_num",
    ] = (
        result_df.loc[
            candidate_mask,
            "_print_base",
        ]
        .astype(str)
    )

    # A family is separated by color. A red plain base and blue printed part
    # are not placed in the same summary group.
    result_df["_print_color_key"] = result_df[
        "color_id"
    ].apply(
        lambda value: (
            "none"
            if pd.isna(value)
            else str(int(value))
        )
    )

    result_df.loc[
        candidate_mask,
        "print_family_id",
    ] = (
        "P:"
        + result_df.loc[
            candidate_mask,
            "print_family_base_part_num",
        ]
        + ":C:"
        + result_df.loc[
            candidate_mask,
            "_print_color_key",
        ]
    )

    # Do not create a family header for only one official inventory part.
    # A valid family needs at least two different part numbers, such as a
    # plain base plus a printed version.
    family_member_counts = (
        result_df.loc[candidate_mask]
        .groupby("print_family_id")["part_num"]
        .nunique()
    )

    valid_family_ids = set(
        family_member_counts[
            family_member_counts >= 2
        ].index
    )

    valid_family_mask = result_df[
        "print_family_id"
    ].isin(valid_family_ids)

    result_df.loc[
        valid_family_mask,
        "has_print_family",
    ] = True

    plain_member_mask = (
        valid_family_mask
        & (
            result_df["part_num"]
            == result_df["print_family_base_part_num"]
        )
    )

    printed_member_mask = (
        valid_family_mask
        & ~plain_member_mask
    )

    result_df.loc[
        plain_member_mask,
        "print_family_role",
    ] = "plain_base"

    result_df.loc[
        printed_member_mask,
        "print_family_role",
    ] = "printed_part"

    # Clear temporary metadata for candidates that did not form a real family.
    invalid_family_mask = (
        candidate_mask
        & ~valid_family_mask
    )

    result_df.loc[
        invalid_family_mask,
        [
            "print_family_id",
            "print_family_base_part_num",
            "print_family_role",
        ],
    ] = ""

    return result_df.drop(
        columns=[
            "_print_base",
            "_print_color_key",
        ]
    )

# =============================================================================
# Attach counterpart information to the official inventory and sort direct
# counterparts near one another.
#
# Relationship rows are summarized before the merge so a part belonging to
# several groups does not duplicate an official part/color row or its quantity.
# Local processing only. No API calls.
# =============================================================================
def attach_counterpart_groups(parts_df, relationships_df):
    parts_display_df = parts_df.copy()
    parts_display_df["part_num"] = parts_display_df["part_num"].astype(str)
    if "color_id" in parts_display_df.columns:
        parts_display_df["_counterpart_color_key"] = parts_display_df[
            "color_id"
        ].apply(_counterpart_color_key)
    else:
        parts_display_df["_counterpart_color_key"] = "none"
    parts_display_df["_original_order"] = range(len(parts_display_df))

    counterpart_groups_df = build_counterpart_groups(parts_display_df, relationships_df)

    # Return consistent columns when this set has no counterpart relationships.
    if counterpart_groups_df.empty:
        parts_display_df["counterpart_group_id"] = ""
        parts_display_df["counterpart"] = ""
        parts_display_df["has_counterpart"] = False
        result_df = parts_display_df.drop(
            columns=["_counterpart_color_key", "_original_order"]
        ).reset_index(drop=True)

        return result_df

    relationship_labels = {
        "P": "Print",
        "R": "Pair",
        "B": "Subpart"}
    role_labels = {
        "plain_base": "plain base",
        "printed_part": "printed part",
        "pair_member": "pair member",
        "assembly": "assembly",
        "subpart": "subpart",
        "counterpart": "counterpart"}

    counterpart_groups_df = counterpart_groups_df.copy()
    counterpart_groups_df["counterpart"] = counterpart_groups_df.apply(
        lambda row: (
            f"{relationship_labels.get(row['rel_type'], row['rel_type'])}: "
            f"{role_labels.get(row['member_role'], row['member_role'])}"), axis=1)

    # Anchor each group at the earliest position occupied by a required member.
    part_order = (
        parts_display_df.groupby([
            "part_num",
            "_counterpart_color_key",
        ])["_original_order"]
        .min()
        .to_dict()
    )
    counterpart_groups_df["_member_order"] = [
        part_order.get((part_num, color_key))
        for part_num, color_key in zip(
            counterpart_groups_df["member_part_num"],
            counterpart_groups_df["_counterpart_color_key"],
        )
    ]
    group_order = (counterpart_groups_df.groupby("counterpart_group_id")["_member_order"].min().to_dict())
    counterpart_groups_df["_group_order"] = (counterpart_groups_df["counterpart_group_id"].map(group_order))

    def join_unique(values):
        """Join values without repeating relationship labels or IDs."""
        return ", ".join(dict.fromkeys(values.dropna().astype(str)))

    counterpart_summary_df = (counterpart_groups_df.groupby(
        ["member_part_num", "_counterpart_color_key"],
        as_index=False,
    ).agg(
            counterpart_group_id=("counterpart_group_id", join_unique),
            counterpart=("counterpart", join_unique),
            _counterpart_sort_order=("_group_order", "min")))
    counterpart_summary_df["has_counterpart"] = True

    parts_display_df = parts_display_df.merge(
        counterpart_summary_df,
        left_on=["part_num", "_counterpart_color_key"],
        right_on=["member_part_num", "_counterpart_color_key"],
        how="left",
    )
    
    parts_display_df = parts_display_df.drop(columns=["member_part_num"])

    parts_display_df["counterpart_group_id"] = (parts_display_df["counterpart_group_id"].fillna(""))
    parts_display_df["counterpart"] = (parts_display_df["counterpart"].fillna(""))
    parts_display_df["has_counterpart"] = (parts_display_df["has_counterpart"].fillna(False).astype(bool))
    parts_display_df["_counterpart_sort_order"] = (parts_display_df["_counterpart_sort_order"]
        .fillna(parts_display_df["_original_order"]))

    # Unrelated rows keep their original order. Related rows move beside the
    # earliest required member of their group.
    result_df = (parts_display_df.sort_values(by=["_counterpart_sort_order", "_original_order"],
            kind="stable").drop(columns=[
                "_counterpart_color_key",
                "_counterpart_sort_order",
                "_original_order",
            ]).reset_index(drop=True))

    return result_df
