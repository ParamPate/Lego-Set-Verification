'''
Compare what a user has against a set's official part list and build a
report covering missing, extra, and not-in-set parts.
'''

from pathlib import Path
import xml.etree.ElementTree as ET
import pandas as pd

# =============================================================================
# Outer join so parts the user has but the set doesn't need still show up 
# (as 'Not in set'), not just parts the set needs but the user doesn't have.
# =============================================================================
def compare_parts(required_parts_df, user_have_df):
    key_cols = ["part_num", "color_id"]

    required = required_parts_df.copy()
    required["part_num"] = required["part_num"].astype(str)
    required["color_id"] = pd.to_numeric(required["color_id"], errors="coerce").astype("Int64")

    aggregations = {
        "part_name": "first",
        "color_name": "first",
        "image_url": "first",
        "quantity_required": "sum"}

    # Carry BrickLink identifiers through when the inventory has them, so the
    # report and the Wanted List export can show client-facing values.
    for column in ["bricklink_part_num", "bricklink_color_id"]:
        if column in required.columns:
            aggregations[column] = "first"

    required = required.groupby(
        key_cols,
        as_index=False,
        dropna=False,
    ).agg(aggregations)

    have = user_have_df.copy()
    have["part_num"] = have["part_num"].astype(str)
    have["color_id"] = pd.to_numeric(have["color_id"], errors="coerce").astype("Int64")
    if "bricklink_color_id" not in have.columns:
        have["bricklink_color_id"] = pd.NA
    have["bricklink_color_id"] = pd.to_numeric(
        have["bricklink_color_id"], errors="coerce"
    ).astype("Int64")
    have = have.groupby(
        [*key_cols, "bricklink_color_id"],
        as_index=False,
        dropna=False,
    ).agg({"quantity_have": "sum"})
    have = have.rename(columns={
        "bricklink_color_id": "_source_bricklink_color_id",
    })

    comparison = required.merge(have, on=key_cols, how="outer")
    if "bricklink_color_id" not in comparison.columns:
        comparison["bricklink_color_id"] = pd.NA
    comparison["bricklink_color_id"] = pd.to_numeric(
        comparison["bricklink_color_id"], errors="coerce"
    ).astype("Int64")
    comparison["bricklink_color_id"] = comparison[
        "bricklink_color_id"
    ].fillna(comparison["_source_bricklink_color_id"])

    unmapped_import_color = (
        comparison["color_id"].isna()
        & comparison["_source_bricklink_color_id"].notna()
    )
    comparison.loc[
        unmapped_import_color & comparison["color_name"].isna(),
        "color_name",
    ] = "No Rebrickable color match"
    comparison = comparison.drop(columns=["_source_bricklink_color_id"])
    comparison["quantity_required"] = comparison["quantity_required"].fillna(0).astype(int)
    comparison["quantity_have"] = comparison["quantity_have"].fillna(0).astype(int)

    comparison["quantity_missing"] = (
        comparison["quantity_required"] - comparison["quantity_have"]).clip(lower=0)
    comparison["quantity_extra"] = (
        comparison["quantity_have"] - comparison["quantity_required"]).clip(lower=0)

    comparison["status"] = comparison.apply(_status, axis=1)

    return comparison.sort_values(by=["status", "part_num"]).reset_index(drop=True)


def _status(row):
    if row["quantity_required"] == 0 and row["quantity_have"] > 0:
        return "Not in set"
    if row["quantity_missing"] > 0:
        return "Missing"
    if row["quantity_extra"] > 0:
        return "Extra"
    return "Complete"

# =============================================================================
# One combined report: every comparison row that is not Complete.
# =============================================================================
def build_full_report(comparison_df):
    report_columns = [
        "image_url",
        "bricklink_part_num",
        "bricklink_part_candidates",
        "bricklink_color_id",
        "part_num",
        "part_name",
        "color_id",
        "color_name",
        "quantity_required",
        "quantity_have",
        "quantity_missing",
        "quantity_extra",
        "status",
        "wanted_list_remark",
        "variation_note",
    ]

    report_rows = comparison_df[comparison_df["status"] != "Complete"].copy()

    for column in report_columns:
        if column not in report_rows.columns:
            report_rows[column] = pd.NA

    return report_rows[report_columns]


# =============================================================================
# Bytes-generating functions are the building blocks, reused for writing a file to disk
# =============================================================================
def report_to_csv_bytes(report_df):
    return report_df.to_csv(index=False).encode("utf-8")


# =============================================================================
# Rows eligible for a BrickLink Wanted List of parts that are still missing.
#
# Complete, Extra, and Not in set rows stay out of the Wanted List. Anything
# missing a BrickLink translation is returned separately so the caller can
# report it rather than quietly dropping it from the export.
# =============================================================================
def select_wanted_list_rows(comparison_df):
    missing_rows = comparison_df[
        pd.to_numeric(
            comparison_df["quantity_missing"], errors="coerce"
        ).fillna(0) > 0
    ].copy()

    if missing_rows.empty:
        return missing_rows, missing_rows

    for column in [
        "bricklink_part_num",
        "bricklink_part_candidates",
        "bricklink_color_id",
    ]:
        if column not in missing_rows.columns:
            missing_rows[column] = pd.NA

    part_nums = (
        missing_rows["bricklink_part_num"]
        .astype("string")
        .fillna("")
        .str.strip()
    )
    color_ids = pd.to_numeric(
        missing_rows["bricklink_color_id"], errors="coerce"
    ).astype("Int64")

    exportable_mask = (part_nums != "") & color_ids.notna()

    return (
        missing_rows[exportable_mask.values].copy(),
        missing_rows[(~exportable_mask).values].copy(),
    )


# =============================================================================
# Build a BrickLink Wanted List for upload.
#
# BrickLink rejects a file that carries an XML declaration, so this writes the
# bare <INVENTORY> element. Only missing parts are included, and MINQTY is the
# remaining missing quantity. Variation allocations count toward the user's
# owned quantity before this function runs.
# =============================================================================
def report_to_bricklink_xml_bytes(comparison_df, condition="U"):
    exportable_rows, _skipped_rows = select_wanted_list_rows(comparison_df)

    if exportable_rows.empty:
        raise ValueError(
            "No missing parts have a BrickLink part number and color, "
            "so there is nothing to put on a wanted list."
        )

    root = ET.Element("INVENTORY")

    for _, row in exportable_rows.iterrows():
        item = ET.SubElement(root, "ITEM")

        ET.SubElement(item, "ITEMTYPE").text = "P"
        ET.SubElement(item, "ITEMID").text = str(
            row["bricklink_part_num"]
        ).strip()
        ET.SubElement(item, "COLOR").text = str(
            int(row["bricklink_color_id"])
        )
        quantity_missing = int(pd.to_numeric(
            row.get("quantity_missing"), errors="coerce"
        ))
        ET.SubElement(item, "MINQTY").text = str(quantity_missing)

        if condition:
            ET.SubElement(item, "CONDITION").text = str(condition)

        remark = row.get("wanted_list_remark", "")
        if remark is None or pd.isna(remark):
            remark = ""
        ET.SubElement(item, "REMARKS").text = str(remark)

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


# =============================================================================
# If path already exists, append _1, _2, ... until a free name is found 
# (report.csv -> report_1.csv...)
# =============================================================================
def _unique_path(path):
    """."""
    path = Path(path)
    if not path.exists():
        return path
 
    counter = 1
    while True:
        candidate = path.with_stem(f"{path.stem}_{counter}")
        if not candidate.exists():
            return candidate
        counter += 1

# =============================================================================
# Export the report to disk.
#
# fmt: 'csv' for the attention-only verification report, or 'xml' for a
# BrickLink Wanted List containing only missing quantities.
# =============================================================================
def export_report(
        report_df,
        path,
        fmt="csv",
        overwrite=False,
        condition="U"):

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not overwrite:
        path = _unique_path(path)

    if fmt == "csv":
        data = report_to_csv_bytes(report_df)
    elif fmt == "xml":
        data = report_to_bricklink_xml_bytes(
            report_df,
            condition=condition,
        )
    else:
        raise ValueError(f"Unsupported format: {fmt!r}. Use 'csv' or 'xml'.")

    path.write_bytes(data)
    return path
