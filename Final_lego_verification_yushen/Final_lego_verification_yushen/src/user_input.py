'''
Gather the parts a user has from manual entry and/or a Brickognize export.

A Brickognize export holds one row per photographed part, so quantities come
from counting rows that share a part and color. Only rows a reviewer marked
'Ready' are counted, and the reviewed 'Final ...' columns are read rather than
the raw predictions, because a reviewer may have corrected them.
'''

from dataclasses import dataclass, field

import pandas as pd

from src.bricklink_mapping import (
    UNRESOLVED,
    load_color_map,
    map_bricklink_color_ids,
    resolve_bricklink_part_nums,
)

# Columns the importer reads from a Brickognize export.
part_id_column = "Final Part ID"
color_id_column = "Final BrickLink Color ID"
review_status_column = "Final Review Status"
type_column = "Type"
part_name_column = "Final Part Name"

required_csv_columns = {
    part_id_column,
    color_id_column,
    review_status_column,
}

ready_review_status = "ready"
part_row_type = "part"


@dataclass
class ImportSummary:
    """Counts and warnings describing what an import actually took in."""

    source_name: str = ""
    total_rows: int = 0
    counted_rows: int = 0
    skipped_not_ready: int = 0
    skipped_not_part: int = 0
    skipped_incomplete: int = 0
    total_quantity: int = 0
    distinct_combinations: int = 0
    unmapped_color_ids: list = field(default_factory=list)
    unresolved_part_nums: list = field(default_factory=list)
    translated_part_nums: list = field(default_factory=list)

    @property
    def skipped_rows(self):
        return (
            self.skipped_not_ready
            + self.skipped_not_part
            + self.skipped_incomplete
        )

    @property
    def has_warnings(self):
        # Rows that did not count are worth an explicit warning: a silently
        # dropped row looks exactly like a part the operator never scanned.
        return bool(
            self.unmapped_color_ids
            or self.unresolved_part_nums
            or self.skipped_rows
        )

    def as_lines(self):
        """Human-readable summary lines for the import dialog."""
        lines = [
            f"Rows in file: {self.total_rows}",
            f"Rows counted: {self.counted_rows}",
            f"Parts counted: {self.total_quantity}",
            f"Distinct part/color combinations: {self.distinct_combinations}",
        ]

        if self.skipped_rows:
            lines.append(f"Rows skipped: {self.skipped_rows}")

            if self.skipped_not_ready:
                lines.append(
                    f"  - not marked Ready: {self.skipped_not_ready}"
                )
            if self.skipped_not_part:
                lines.append(
                    f"  - not a part row: {self.skipped_not_part}"
                )
            if self.skipped_incomplete:
                lines.append(
                    f"  - missing a part number or color: "
                    f"{self.skipped_incomplete}"
                )

        if self.translated_part_nums:
            lines.append(
                f"BrickLink part numbers translated: "
                f"{len(self.translated_part_nums)}"
            )

        if self.unresolved_part_nums:
            lines.append(
                "Part numbers with no Rebrickable match "
                f"({len(self.unresolved_part_nums)}): "
                + ", ".join(self.unresolved_part_nums[:10])
                + ("..." if len(self.unresolved_part_nums) > 10 else "")
            )

        if self.unmapped_color_ids:
            lines.append(
                "BrickLink color IDs with no Rebrickable match "
                f"({len(self.unmapped_color_ids)}): "
                + ", ".join(str(color) for color in self.unmapped_color_ids[:10])
                + ("..." if len(self.unmapped_color_ids) > 10 else "")
            )

        return lines


# =============================================================================
# Read a Brickognize export into inventory rows.
#
# Returns (have_df, summary). Rows whose part number or color cannot be
# translated are kept so nothing disappears silently. For an unmapped color,
# color_id remains NA and bricklink_color_id retains the reviewed source ID;
# the summary names those IDs so the user can follow up.
# =============================================================================
def load_from_csv(csv_source, source_name=""):
    raw_df = pd.read_csv(csv_source, dtype=str)

    missing_cols = required_csv_columns - set(raw_df.columns)
    if missing_cols:
        raise ValueError(
            "CSV is missing required columns: "
            f"{sorted(missing_cols)}. Expected a Brickognize export."
        )

    summary = ImportSummary(
        source_name=source_name,
        total_rows=len(raw_df),
    )

    df = raw_df.copy()

    # Only count rows a reviewer signed off on.
    review_status = (
        df[review_status_column].fillna("").astype(str).str.strip().str.casefold()
    )
    ready_mask = review_status == ready_review_status
    summary.skipped_not_ready = int((~ready_mask).sum())
    df = df[ready_mask]

    # A minifig or set row is not an inventory part. A blank type counts as a
    # part: only an explicit other type is a reason to drop a row, otherwise a
    # hand-edited or trimmed export loses rows for no visible reason.
    if type_column in df.columns:
        row_type = (
            df[type_column].fillna("").astype(str).str.strip().str.casefold()
        )
        part_mask = row_type.isin(["", part_row_type])
        summary.skipped_not_part = int((~part_mask).sum())
        df = df[part_mask]

    df = df.copy()
    df["_bricklink_part_num"] = (
        df[part_id_column].fillna("").astype(str).str.strip()
    )
    df["_bricklink_color_id"] = pd.to_numeric(
        df[color_id_column],
        errors="coerce",
    ).astype("Int64")

    complete_mask = (
        (df["_bricklink_part_num"] != "")
        & df["_bricklink_color_id"].notna()
    )
    summary.skipped_incomplete = int((~complete_mask).sum())
    df = df[complete_mask].copy()

    summary.counted_rows = len(df)

    empty_have_df = pd.DataFrame(
        columns=[
            "part_num",
            "color_id",
            "bricklink_color_id",
            "quantity_have",
        ]
    )

    if df.empty:
        return empty_have_df, summary

    # One row per photograph, so the row count is the quantity.
    grouped_df = (
        df.groupby(
            ["_bricklink_part_num", "_bricklink_color_id"],
            as_index=False,
            dropna=False,
        )
        .size()
        .rename(columns={"size": "quantity_have"})
    )

    color_map_df = load_color_map()
    mapped_color_ids = map_bricklink_color_ids(
        grouped_df["_bricklink_color_id"],
        color_map_df,
    )

    unmapped_color_mask = mapped_color_ids.isna()
    summary.unmapped_color_ids = sorted(
        int(color_id)
        for color_id in grouped_df.loc[
            unmapped_color_mask.values,
            "_bricklink_color_id",
        ]
        .dropna()
        .unique()
    )

    grouped_df["color_id"] = mapped_color_ids.values
    # Keep the source ID only when there is no Rebrickable translation. Mapped
    # rows continue to use the Rebrickable color as their internal identity.
    grouped_df["bricklink_color_id"] = grouped_df[
        "_bricklink_color_id"
    ].where(unmapped_color_mask.values, pd.NA).astype("Int64")

    part_map_df = resolve_bricklink_part_nums(
        grouped_df["_bricklink_part_num"]
    )
    part_lookup = dict(
        zip(
            part_map_df["bricklink_part_num"],
            part_map_df["rebrickable_part_num"],
        )
    )
    resolution_lookup = dict(
        zip(
            part_map_df["bricklink_part_num"],
            part_map_df["resolved_via"],
        )
    )

    summary.unresolved_part_nums = sorted(
        part_num
        for part_num, resolved_via in resolution_lookup.items()
        if resolved_via == UNRESOLVED
    )
    summary.translated_part_nums = sorted(
        (part_num, mapped)
        for part_num, mapped in part_lookup.items()
        if mapped and mapped != part_num
    )

    # Fall back to the BrickLink number so an unmapped part still reaches the
    # report rather than vanishing during import.
    grouped_df["part_num"] = [
        part_lookup.get(part_num) or part_num
        for part_num in grouped_df["_bricklink_part_num"]
    ]

    have_df = grouped_df[[
        "part_num",
        "color_id",
        "bricklink_color_id",
        "quantity_have",
    ]].copy()

    # Two BrickLink numbers can collapse onto one Rebrickable part.
    have_df = combine_entry_csv(have_df)

    summary.total_quantity = int(have_df["quantity_have"].sum())
    summary.distinct_combinations = len(have_df)

    return have_df, summary


# =============================================================================
# Combine user entries with CSV files
# =============================================================================
def combine_entry_csv(*have_dfs):
    frames = [df for df in have_dfs if df is not None and not df.empty]

    if not frames:
        return pd.DataFrame(columns=[
            "part_num",
            "color_id",
            "bricklink_color_id",
            "quantity_have",
        ])

    combined = pd.concat(frames, ignore_index=True)
    if "bricklink_color_id" not in combined.columns:
        combined["bricklink_color_id"] = pd.NA
    combined["part_num"] = combined["part_num"].astype(str).str.strip()
    combined["color_id"] = pd.to_numeric(
        combined["color_id"], errors="coerce").astype("Int64")
    combined["bricklink_color_id"] = pd.to_numeric(
        combined["bricklink_color_id"], errors="coerce").astype("Int64")
    combined["quantity_have"] = pd.to_numeric(
        combined["quantity_have"], errors="coerce").fillna(0).astype(int)
    return combined.groupby(
        ["part_num", "color_id", "bricklink_color_id"],
        as_index=False,
        dropna=False)["quantity_have"].sum()


# =============================================================================
# Fill only unentered quantities from a CSV prefill.
#
# A current quantity of zero is treated as blank. Existing nonzero quantities
# are preserved, while CSV-only rows are added so parts not listed in the
# official set can still appear in the final report.
# =============================================================================
def fill_blank_quantities(current_have_df, prefill_have_df):
    current = combine_entry_csv(current_have_df)
    prefill = combine_entry_csv(prefill_have_df)

    if current.empty:
        return prefill

    if prefill.empty:
        return current

    merged = current.merge(
        prefill,
        on=["part_num", "color_id", "bricklink_color_id"],
        how="outer",
        suffixes=("_current", "_prefill"),
    )

    merged["quantity_have_current"] = (
        merged["quantity_have_current"].fillna(0).astype(int)
    )
    merged["quantity_have_prefill"] = (
        merged["quantity_have_prefill"].fillna(0).astype(int)
    )

    # Preserve an existing nonzero value; otherwise use the CSV quantity.
    merged["quantity_have"] = merged["quantity_have_current"].where(
        merged["quantity_have_current"] > 0,
        merged["quantity_have_prefill"],
    )

    return merged[
        ["part_num", "color_id", "bricklink_color_id", "quantity_have"]
    ].reset_index(drop=True)
