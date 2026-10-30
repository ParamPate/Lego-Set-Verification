"""
Create a polished Excel report from SK BrickRescue result files.

This module does not change the CSV or JSON source files. It only reads them
and creates a new Excel workbook containing a dashboard, batch details,
manual-review history, API audit information, and instructions.

The desktop interface calls ``create_excel_report()`` whenever an employee
chooses either Excel export option.
"""

# ---------------------------------------------------------------------------
# MAINTAINER MAP: EXCEL REPORT GENERATION
# ---------------------------------------------------------------------------
# This module is read-only with respect to operational CSV/JSON data. It creates
# a new timestamped workbook and never uses Excel as the application database.
#
# Workbook construction order:
# 1. Read active CSV, employee-review JSON, and API-response JSON.
# 2. Normalize predicted and final fields with _prepare_final_rows().
# 3. Create the Dashboard summary.
# 4. Create Batch Results with one operational row per image.
# 5. Create Manual Reviews from saved decisions.
# 6. Create API Audit for traceable response metadata.
# 7. Create Instructions so a shared workbook remains understandable.
# 8. Apply filters, tables, freeze panes, print settings, and formatting.
#
# Important rules:
# - Export reads a snapshot. Later GUI changes require a new Excel export.
# - Missing optional JSON files are allowed; a missing/empty result CSV is not.
# - Predicted fields and Final fields stay separate for auditability.
# - Confidence values are percentages; BrickLink color IDs are text.
# - Worksheet names and column labels may be referenced by employees, so rename
#   them only with matching README and workbook-instruction updates.
# - Logo loading is optional. A missing logo must not block report creation.
# ---------------------------------------------------------------------------

from __future__ import annotations

import csv
import json
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


# ---------------------------------------------------------------------------
# Source files and workbook design constants
# ---------------------------------------------------------------------------

# The file will live inside src/, so its parent.parent is the project folder.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_FOLDER = PROJECT_ROOT / "results"
CSV_FILE = RESULTS_FOLDER / "brickognize_results.csv"
REVIEWS_FILE = RESULTS_FOLDER / "brickognize_reviews.json"
API_RESPONSES_FILE = RESULTS_FOLDER / "brickognize_responses.json"
LOGO_FILE = PROJECT_ROOT / "assets" / "sk_brickrescue_logo.png"


# Shared workbook colors.
NAVY = "173653"
BLUE = "0B35D5"
GREEN = "009900"
LIGHT_GREEN = "D5F5DF"
YELLOW = "FFF1C7"
RED = "FAD7D9"
LIGHT_BLUE = "E7EFFB"
LIGHT_GRAY = "F4F6F8"
MID_GRAY = "64748B"
WHITE = "FFFFFF"
BORDER_COLOR = "D6DEE7"

THIN_BORDER = Border(
    left=Side(style="thin", color=BORDER_COLOR),
    right=Side(style="thin", color=BORDER_COLOR),
    top=Side(style="thin", color=BORDER_COLOR),
    bottom=Side(style="thin", color=BORDER_COLOR),
)


# ---------------------------------------------------------------------------
# Source-data preparation
# ---------------------------------------------------------------------------

# FUNCTION GUIDE: _read_csv_rows
# Purpose: Read the shared result CSV and return one dictionary per image.
def _read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read the shared result CSV and return one dictionary per image."""

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Result file was not found: {csv_path}"
        )

    with csv_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as csv_file:
        return list(csv.DictReader(csv_file))


# FUNCTION GUIDE: _read_json_object
# Purpose: Read a JSON object, returning an empty object when the file is absent.
def _read_json_object(json_path: Path) -> dict[str, Any]:
    """Read a JSON object, returning an empty object when the file is absent."""

    if not json_path.exists():
        return {}

    try:
        with json_path.open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)
    except (json.JSONDecodeError, OSError):
        return {}

    return data if isinstance(data, dict) else {}


# FUNCTION GUIDE: _percentage_number
# Purpose: Convert values such as '82.84%' into Excel's numeric percentage form.
def _percentage_number(value: Any) -> float | None:
    """Convert values such as '82.84%' into Excel's numeric percentage form."""

    if value in (None, "", "Unknown"):
        return None

    try:
        text = str(value).strip().replace("%", "")
        return float(text) / 100
    except (TypeError, ValueError):
        return None


# FUNCTION GUIDE: _get_review_sections
# Purpose: Return the color-review and part-review portions of a saved review.
def _get_review_sections(
    review_value: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Return the color-review and part-review portions of a saved review.

    Older color reviews used a flat format. Newer records group decisions
    under "Color Review" and "Part Review", so both formats are supported.
    """

    if not isinstance(review_value, dict):
        return {}, {}

    color_review = review_value.get("Color Review", {})
    part_review = review_value.get("Part Review", {})

    if not isinstance(color_review, dict):
        color_review = {}

    if not isinstance(part_review, dict):
        part_review = {}

    if (
        not color_review
        and "Confirmed Color" in review_value
    ):
        color_review = review_value

    return color_review, part_review


# FUNCTION GUIDE: _prepare_final_rows
# Purpose: Combine original predictions with completed employee decisions.
def _prepare_final_rows(
    csv_rows: list[dict[str, str]],
    saved_reviews: dict[str, Any],
) -> list[dict[str, Any]]:
    """Combine original predictions with completed employee decisions."""

    prepared_rows = []

    for original in csv_rows:
        image_name = original.get("Image", "Unknown")
        color_review, part_review = _get_review_sections(
            saved_reviews.get(image_name, {})
        )

        final_part_id = (
            part_review.get("Final Part ID")
            or original.get("Part ID")
            or "Unknown"
        )
        final_part_name = (
            part_review.get("Final Part Name")
            or original.get("Part Name")
            or "Unknown"
        )
        final_part_confidence = (
            part_review.get("Final Part Confidence")
            or original.get("Part Confidence")
            or ""
        )
        final_color = (
            color_review.get("Confirmed Color")
            or original.get("LEGO Color")
            or "Unknown"
        )
        predicted_color_id = (
            original.get("Predicted BrickLink Color ID")
            or original.get("BrickLink Color ID")
            or ""
        )
        final_color_id = (
            color_review.get("Confirmed BrickLink Color ID")
            or original.get("Final BrickLink Color ID")
            or predicted_color_id
        )

        part_review_completed = bool(part_review)
        color_review_completed = bool(color_review)

        part_review_required = (
            original.get("Part Review Required", "No") == "Yes"
            and not part_review_completed
        )
        color_review_required = (
            original.get("Color Review Required", "No") == "Yes"
            and not color_review_completed
        )

        if part_review_required and color_review_required:
            final_status = "Part and Color Checks Suggested"
        elif part_review_required:
            final_status = "Part Check Suggested"
        elif color_review_required:
            final_status = "Color Check Suggested"
        else:
            final_status = "Ready"

        if part_review_required:
            part_approval_status = "Optional Check Suggested"
        elif part_review_completed:
            part_approval_status = (
                "Corrected"
                if part_review.get("Decision") == "Use Rescan Result"
                else "Confirmed"
            )
        else:
            part_approval_status = original.get(
                "Part Approval Status",
                "Auto-Accepted",
            )

        if color_review_required:
            color_approval_status = "Optional Check Suggested"
        elif color_review_completed:
            color_approval_status = color_review.get(
                "Color Approval Status",
                (
                    "Confirmed"
                    if final_color == original.get("LEGO Color")
                    else "Corrected"
                ),
            )
        else:
            color_approval_status = original.get(
                "Color Approval Status",
                "Auto-Accepted",
            )

        bricklink_url = original.get("BrickLink URL", "")
        if final_part_id not in ("", "Unknown"):
            bricklink_url = (
                "https://www.bricklink.com/v2/catalog/"
                f"catalogitem.page?P={final_part_id}"
            )

        prepared_rows.append(
            {
                **original,
                "Original Part ID": original.get("Part ID", ""),
                "Original Part Name": original.get("Part Name", ""),
                "Original Part Confidence": original.get(
                    "Part Confidence",
                    "",
                ),
                "Final Part ID": final_part_id,
                "Final Part Name": final_part_name,
                "Final Part Confidence": final_part_confidence,
                "Original LEGO Color": original.get("LEGO Color", ""),
                "Predicted BrickLink Color ID": predicted_color_id,
                "Final LEGO Color": final_color,
                "Final BrickLink Color ID": final_color_id,
                "Part Approval Status": part_approval_status,
                "Color Approval Status": color_approval_status,
                "Part Review Completed": (
                    "Yes" if part_review_completed else "No"
                ),
                "Color Review Completed": (
                    "Yes" if color_review_completed else "No"
                ),
                "Part Check Suggested": (
                    "Yes" if part_review_required else "No"
                ),
                "Color Check Suggested": (
                    "Yes" if color_review_required else "No"
                ),
                "Final Review Status": final_status,
                "Final BrickLink URL": bricklink_url,
            }
        )

    return prepared_rows


# ---------------------------------------------------------------------------
# Reusable worksheet formatting
# ---------------------------------------------------------------------------

# FUNCTION GUIDE: _style_title
# Purpose: Add the report title, subtitle, and optional company logo.
def _style_title(
    sheet,
    title: str,
    subtitle: str,
    final_column: int,
) -> None:
    """Add the report title, subtitle, and optional company logo."""

    sheet.merge_cells(
        start_row=1,
        start_column=1,
        end_row=1,
        end_column=final_column,
    )
    title_cell = sheet.cell(1, 1, title)
    title_cell.font = Font(
        name="Segoe UI",
        size=22,
        bold=True,
        color=NAVY,
    )
    title_cell.alignment = Alignment(horizontal="center")

    sheet.merge_cells(
        start_row=2,
        start_column=1,
        end_row=2,
        end_column=final_column,
    )
    subtitle_cell = sheet.cell(2, 1, subtitle)
    subtitle_cell.font = Font(
        name="Segoe UI",
        size=10,
        color=MID_GRAY,
    )
    subtitle_cell.alignment = Alignment(horizontal="center")

    sheet.row_dimensions[1].height = 32
    sheet.row_dimensions[2].height = 22

    if LOGO_FILE.exists():
        try:
            # Load logo bytes into memory so Windows and alternate launch
            # not retain an open handle to the source PNG.
            logo = ExcelImage(BytesIO(LOGO_FILE.read_bytes()))
            logo.width = 52
            logo.height = 52
            sheet.add_image(logo, "A1")
        except (OSError, ValueError):
            # The report remains usable if the logo cannot be loaded.
            pass


# FUNCTION GUIDE: _style_header_row
# Purpose: Apply the standard dark-blue table heading style.
def _style_header_row(sheet, row_number: int) -> None:
    """Apply the standard dark-blue table heading style."""

    for cell in sheet[row_number]:
        if cell.value is None:
            continue

        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(
            name="Segoe UI",
            size=10,
            bold=True,
            color=WHITE,
        )
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )
        cell.border = THIN_BORDER

    sheet.row_dimensions[row_number].height = 34


# FUNCTION GUIDE: _configure_print_layout
# Purpose: Keep reports readable when printed or saved as PDF.
def _configure_print_layout(
    sheet,
    print_area: str,
    *,
    landscape: bool = True,
) -> None:
    """Keep reports readable when printed or saved as PDF."""

    sheet.print_area = print_area
    sheet.page_setup.orientation = (
        "landscape" if landscape else "portrait"
    )
    sheet.page_setup.paperSize = sheet.PAPERSIZE_LETTER
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_margins.left = 0.25
    sheet.page_margins.right = 0.25
    sheet.page_margins.top = 0.5
    sheet.page_margins.bottom = 0.5


# FUNCTION GUIDE: _finish_data_sheet
# Purpose: Finish sizing, filtering, freezing, and table styling.
def _finish_data_sheet(
    sheet,
    header_row: int,
    last_row: int,
    last_column: int,
    table_name: str,
) -> None:
    """Finish sizing, filtering, freezing, and table styling."""

    sheet.freeze_panes = sheet.cell(header_row + 1, 1)

    if last_row > header_row:
        # The Excel table supplies its own filter arrows. Adding a separate
        # worksheet-level AutoFilter over the same cells makes desktop Excel
        # treat the table definition as unreadable.
        table = Table(
            displayName=table_name,
            ref=(
                f"A{header_row}:"
                f"{sheet.cell(last_row, last_column).coordinate}"
            ),
        )
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        sheet.add_table(table)

    for row in sheet.iter_rows(
        min_row=header_row + 1,
        max_row=last_row,
        max_col=last_column,
    ):
        for cell in row:
            cell.font = Font(name="Segoe UI", size=9)
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=True,
            )
            cell.border = THIN_BORDER

    # Use sensible widths rather than letting long URLs stretch the sheet.
    for column_number, column_cells in enumerate(
        sheet.iter_cols(
            min_col=1,
            max_col=last_column,
        ),
        start=1,
    ):
        column_letter = get_column_letter(column_number)
        longest = max(
            len(str(cell.value)) if cell.value is not None else 0
            for cell in column_cells
        )
        sheet.column_dimensions[column_letter].width = min(
            max(longest + 2, 12),
            34,
        )

    sheet.sheet_view.showGridLines = False
    _configure_print_layout(
        sheet,
        (
            f"A1:"
            f"{sheet.cell(last_row, last_column).coordinate}"
        ),
    )


# ---------------------------------------------------------------------------
# Workbook sheets
# ---------------------------------------------------------------------------

# FUNCTION GUIDE: _add_dashboard
# Purpose: Create the executive summary and status chart.
def _add_dashboard(
    workbook: Workbook,
    rows: list[dict[str, Any]],
) -> None:
    """Create the executive summary and status chart."""

    sheet = workbook.active
    sheet.title = "Dashboard"
    sheet.sheet_view.showGridLines = False

    exported_at = datetime.now().astimezone().strftime(
        "%Y-%m-%d %H:%M %Z"
    )
    _style_title(
        sheet,
        "SK BrickRescue Batch Report",
        (
            "LEGO Part Recognition & Inventory Support | "
            f"Exported {exported_at}"
        ),
        13,
    )

    counts = {
        "Total Results": len(rows),
        "Ready": sum(
            row["Final Review Status"] == "Ready"
            for row in rows
        ),
        "Part Review": sum(
            row["Final Review Status"]
            in (
                "Part Check Suggested",
                "Part and Color Checks Suggested",
            )
            for row in rows
        ),
        "Color Review": sum(
            row["Final Review Status"]
            in (
                "Color Check Suggested",
                "Part and Color Checks Suggested",
            )
            for row in rows
        ),
        "Reviewed": sum(
            row["Part Review Completed"] == "Yes"
            or row["Color Review Completed"] == "Yes"
            for row in rows
        ),
    }

    kpi_ranges = [
        ("A5:C7", "Total Results", LIGHT_BLUE, NAVY),
        ("D5:F7", "Ready", LIGHT_GREEN, "126A36"),
        ("G5:H7", "Part Review", YELLOW, "9A4100"),
        ("I5:J7", "Color Review", YELLOW, "9A4100"),
        ("K5:M7", "Reviewed", LIGHT_BLUE, BLUE),
    ]

    for cell_range, label, fill_color, font_color in kpi_ranges:
        sheet.merge_cells(cell_range)
        top_left = sheet[cell_range.split(":")[0]]
        top_left.value = f"{counts[label]}\n{label.upper()}"
        top_left.fill = PatternFill("solid", fgColor=fill_color)
        top_left.font = Font(
            name="Segoe UI",
            size=17,
            bold=True,
            color=font_color,
        )
        top_left.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )
        top_left.border = THIN_BORDER

    sheet.row_dimensions[5].height = 18
    sheet.row_dimensions[6].height = 25
    sheet.row_dimensions[7].height = 25

    sheet.merge_cells("A9:F9")
    sheet["A9"] = "Batch Review Status"
    sheet["A9"].fill = PatternFill("solid", fgColor=NAVY)
    sheet["A9"].font = Font(
        name="Segoe UI",
        bold=True,
        color=WHITE,
    )

    status_labels = [
        "Ready",
        "Part Check Suggested",
        "Color Check Suggested",
        "Part and Color Checks Suggested",
    ]
    status_counts = [
        sum(row["Final Review Status"] == label for row in rows)
        for label in status_labels
    ]

    # The chart reads this small visible table.
    sheet["A27"] = "Status"
    sheet["B27"] = "Count"
    for index, (label, count) in enumerate(
        zip(status_labels, status_counts),
        start=28,
    ):
        sheet.cell(index, 1, label)
        sheet.cell(index, 2, count)

    for cell in sheet[27][:2]:
        cell.fill = PatternFill("solid", fgColor=GREEN)
        cell.font = Font(
            name="Segoe UI",
            bold=True,
            color=WHITE,
        )
        cell.alignment = Alignment(horizontal="center")

    # A horizontal bar chart displays reliably in desktop Excel.
    chart = BarChart()
    chart.type = "bar"
    chart.style = 10
    chart.title = "Images by Status"
    chart.y_axis.title = ""
    chart.x_axis.title = "Images"
    chart.height = 7
    chart.width = 13
    chart.legend = None
    chart.add_data(
        Reference(sheet, min_col=2, min_row=27, max_row=31),
        titles_from_data=True,
    )
    chart.set_categories(
        Reference(sheet, min_col=1, min_row=28, max_row=31)
    )
    sheet.add_chart(chart, "A11")

    sheet.merge_cells("G9:M9")
    sheet["G9"] = "Operational Snapshot"
    sheet["G9"].fill = PatternFill("solid", fgColor=NAVY)
    sheet["G9"].font = Font(
        name="Segoe UI",
        bold=True,
        color=WHITE,
    )

    part_values = [
        _percentage_number(row.get("Final Part Confidence"))
        for row in rows
    ]
    color_values = [
        _percentage_number(row.get("Color Confidence"))
        for row in rows
    ]
    part_values = [value for value in part_values if value is not None]
    color_values = [value for value in color_values if value is not None]

    snapshot = [
        (
            "Average Part Confidence",
            sum(part_values) / len(part_values)
            if part_values else None,
            "Average confidence across the exported batch",
        ),
        (
            "Average Color Confidence",
            sum(color_values) / len(color_values)
            if color_values else None,
            "Average predicted LEGO color confidence",
        ),
        (
            "Low Part Confidence",
            sum(value < 0.80 for value in part_values),
            "Results below the 80% review threshold",
        ),
        (
            "Low Color Confidence",
            sum(value < 0.80 for value in color_values),
            "Results below the 80% review threshold",
        ),
    ]

    for column, heading in enumerate(
        ("Metric", "Value", "Meaning"),
        start=7,
    ):
        cell = sheet.cell(11, column, heading)
        cell.fill = PatternFill("solid", fgColor=GREEN)
        cell.font = Font(
            name="Segoe UI",
            bold=True,
            color=WHITE,
        )
        cell.alignment = Alignment(horizontal="center")

    for row_number, values in enumerate(snapshot, start=12):
        for column, value in enumerate(values, start=7):
            cell = sheet.cell(row_number, column, value)
            cell.font = Font(name="Segoe UI", size=9, color=NAVY)
            cell.border = Border(
                bottom=Side(style="thin", color=BORDER_COLOR)
            )

        if isinstance(values[1], float):
            sheet.cell(row_number, 8).number_format = "0.00%"

    sheet.merge_cells("A33:M35")
    sheet["A33"] = (
        "Final values incorporate completed employee reviews. "
        "Original predictions remain available on the Batch Results and "
        "Manual Reviews sheets for traceability."
    )
    sheet["A33"].font = Font(
        name="Segoe UI",
        size=9,
        italic=True,
        color=MID_GRAY,
    )
    sheet["A33"].alignment = Alignment(
        vertical="center",
        wrap_text=True,
    )

    widths = {
        "A": 24,
        "B": 10,
        "C": 12,
        "D": 16,
        "E": 12,
        "F": 12,
        "G": 22,
        "H": 16,
        "I": 18,
        "J": 18,
        "K": 18,
        "L": 12,
        "M": 18,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    sheet.freeze_panes = "A5"
    _configure_print_layout(sheet, "A1:M35")


# FUNCTION GUIDE: _add_batch_results
# Purpose: Create the detailed result sheet.
def _add_batch_results(
    workbook: Workbook,
    rows: list[dict[str, Any]],
) -> None:
    """Create the detailed result sheet."""

    sheet = workbook.create_sheet("Batch Results")
    headers = [
        "Image",
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
        "Original Part ID",
        "Original Part Name",
        "Original Part Confidence",
        "Original LEGO Color",
        "Predicted BrickLink Color ID",
        "Color Confidence",
        "Part Check Suggested",
        "Color Check Suggested",
        "Color Method",
        "Bounding Box Score",
        "Response ID",
        "Reference Image URL",
        "Final BrickLink URL",
    ]

    _style_title(
        sheet,
        "Batch Results",
        "Final decisions and original Brickognize predictions",
        len(headers),
    )

    header_row = 4
    for column, heading in enumerate(headers, start=1):
        sheet.cell(header_row, column, heading)
    _style_header_row(sheet, header_row)

    for row_number, result in enumerate(rows, start=5):
        values = [result.get(header, "") for header in headers]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row_number, column, value)

            heading = headers[column - 1]
            if "Confidence" in heading or heading == "Bounding Box Score":
                numeric_value = _percentage_number(value)
                if numeric_value is not None:
                    cell.value = numeric_value
                    cell.number_format = "0.00%"

        # Resolve link positions from their headings. This stays correct when
        # new audit columns are added to the report.
        for hyperlink_column in (
            headers.index("Reference Image URL") + 1,
            headers.index("Final BrickLink URL") + 1,
        ):
            cell = sheet.cell(row_number, hyperlink_column)
            if cell.value:
                cell.hyperlink = str(cell.value)
                cell.style = "Hyperlink"

    last_row = max(5, len(rows) + 4)
    _finish_data_sheet(
        sheet,
        header_row,
        last_row,
        len(headers),
        "BatchResultsTable",
    )

    status_column = 2
    status_range = (
        f"{sheet.cell(5, status_column).coordinate}:"
        f"{sheet.cell(last_row, status_column).coordinate}"
    )
    sheet.conditional_formatting.add(
        status_range,
        CellIsRule(
            operator="equal",
            formula=['"Ready"'],
            fill=PatternFill("solid", fgColor=LIGHT_GREEN),
        ),
    )


# FUNCTION GUIDE: _add_manual_reviews
# Purpose: Create one audit row for every completed manual decision.
def _add_manual_reviews(
    workbook: Workbook,
    saved_reviews: dict[str, Any],
) -> None:
    """Create one audit row for every completed manual decision."""

    sheet = workbook.create_sheet("Manual Reviews")
    headers = [
        "Image",
        "Review Type",
        "Decision",
        "Approval Status",
        "Original Value",
        "Alternate Value",
        "Final Value",
        "Original BrickLink Color ID",
        "Final BrickLink Color ID",
        "Original Confidence",
        "Alternate Confidence",
        "Reviewed At",
    ]

    _style_title(
        sheet,
        "Manual Reviews",
        "Employee-confirmed color and part decisions",
        len(headers),
    )

    header_row = 4
    for column, heading in enumerate(headers, start=1):
        sheet.cell(header_row, column, heading)
    _style_header_row(sheet, header_row)

    audit_rows = []
    for image_name, review_value in saved_reviews.items():
        color_review, part_review = _get_review_sections(review_value)

        if color_review:
            audit_rows.append(
                [
                    image_name,
                    "Color Review",
                    "Confirmed Color",
                    color_review.get("Color Approval Status", "Confirmed"),
                    color_review.get("Original Color", ""),
                    "",
                    color_review.get("Confirmed Color", ""),
                    color_review.get("Original BrickLink Color ID", ""),
                    color_review.get("Confirmed BrickLink Color ID", ""),
                    color_review.get(
                        "Original Color Confidence",
                        "",
                    ),
                    "",
                    color_review.get("Reviewed At", ""),
                ]
            )

        if part_review:
            audit_rows.append(
                [
                    image_name,
                    "Part Review",
                    part_review.get("Decision", ""),
                    (
                        "Corrected"
                        if part_review.get("Decision") == "Use Rescan Result"
                        else "Confirmed"
                    ),
                    (
                        f"{part_review.get('Original Part ID', '')} - "
                        f"{part_review.get('Original Part Name', '')}"
                    ).strip(" -"),
                    (
                        f"{part_review.get('Rescan Part ID', '')} - "
                        f"{part_review.get('Rescan Part Name', '')}"
                    ).strip(" -"),
                    (
                        f"{part_review.get('Final Part ID', '')} - "
                        f"{part_review.get('Final Part Name', '')}"
                    ).strip(" -"),
                    "",
                    "",
                    part_review.get("Original Part Confidence", ""),
                    part_review.get("Rescan Part Confidence", ""),
                    part_review.get("Reviewed At", ""),
                ]
            )

    for row_number, values in enumerate(audit_rows, start=5):
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row_number, column, value)
            if column in (10, 11):
                numeric_value = _percentage_number(value)
                if numeric_value is not None:
                    cell.value = numeric_value
                    cell.number_format = "0.00%"

    last_row = max(5, len(audit_rows) + 4)
    _finish_data_sheet(
        sheet,
        header_row,
        last_row,
        len(headers),
        "ManualReviewsTable",
    )


# FUNCTION GUIDE: _add_api_audit
# Purpose: Create a compact sheet showing the stored raw-response identifiers.
def _add_api_audit(
    workbook: Workbook,
    api_responses: dict[str, Any],
) -> None:
    """Create a compact sheet showing the stored raw-response identifiers."""

    sheet = workbook.create_sheet("API Audit")
    headers = [
        "Image",
        "Response ID",
        "Query ID",
        "Detected Items",
        "Raw Response Available",
    ]

    _style_title(
        sheet,
        "API Audit",
        "Summary of the shared Brickognize response file",
        len(headers),
    )

    header_row = 4
    for column, heading in enumerate(headers, start=1):
        sheet.cell(header_row, column, heading)
    _style_header_row(sheet, header_row)

    for row_number, (image_name, response) in enumerate(
        sorted(api_responses.items()),
        start=5,
    ):
        response = response if isinstance(response, dict) else {}
        detected_items = response.get("detected_items", [])
        values = [
            image_name,
            response.get("id", ""),
            response.get("query_id", ""),
            len(detected_items) if isinstance(detected_items, list) else 0,
            "Yes",
        ]
        for column, value in enumerate(values, start=1):
            sheet.cell(row_number, column, value)

    last_row = max(5, len(api_responses) + 4)
    _finish_data_sheet(
        sheet,
        header_row,
        last_row,
        len(headers),
        "ApiAuditTable",
    )


# FUNCTION GUIDE: _add_instructions
# Purpose: Explain how the report should be used.
def _add_instructions(workbook: Workbook) -> None:
    """Explain how the report should be used."""

    sheet = workbook.create_sheet("Instructions & Key")
    sheet.sheet_view.showGridLines = False

    _style_title(
        sheet,
        "Instructions & Status Key",
        "How to read the SK BrickRescue batch report",
        8,
    )

    sections = [
        (
            "Purpose",
            "This workbook summarizes one or more Brickognize results and "
            "incorporates completed employee reviews.",
        ),
        (
            "Dashboard",
            "Use the dashboard to see how many images are ready and how many "
            "still require part or color review.",
        ),
        (
            "Batch Results",
            "Final columns reflect accepted employee decisions. Original "
            "prediction columns remain available for comparison.",
        ),
        (
            "Manual Reviews",
            "This sheet records the original prediction, alternate option, "
            "final decision, and review timestamp.",
        ),
        (
            "API Audit",
            "This sheet confirms which raw Brickognize responses are stored "
            "in brickognize_responses.json.",
        ),
        (
            "Important",
            "Generating this workbook does not edit or delete the original "
            "CSV, JSON, review, or image files.",
        ),
    ]

    row_number = 5
    for heading, explanation in sections:
        sheet.merge_cells(
            start_row=row_number,
            start_column=1,
            end_row=row_number,
            end_column=2,
        )
        heading_cell = sheet.cell(row_number, 1, heading)
        heading_cell.fill = PatternFill("solid", fgColor=NAVY)
        heading_cell.font = Font(
            name="Segoe UI",
            bold=True,
            color=WHITE,
        )
        heading_cell.alignment = Alignment(vertical="center")

        sheet.merge_cells(
            start_row=row_number,
            start_column=3,
            end_row=row_number,
            end_column=8,
        )
        explanation_cell = sheet.cell(
            row_number,
            3,
            explanation,
        )
        explanation_cell.font = Font(
            name="Segoe UI",
            size=10,
            color=NAVY,
        )
        explanation_cell.alignment = Alignment(
            vertical="center",
            wrap_text=True,
        )
        explanation_cell.fill = PatternFill(
            "solid",
            fgColor=LIGHT_GRAY,
        )

        sheet.row_dimensions[row_number].height = 46
        row_number += 2

    for column in "ABCDEFGH":
        sheet.column_dimensions[column].width = 18

    _configure_print_layout(
        sheet,
        "A1:H15",
        landscape=False,
    )


# ---------------------------------------------------------------------------
# Public export entry point used by the GUI
# ---------------------------------------------------------------------------

# FUNCTION GUIDE: create_excel_report
# Purpose: Create the complete Excel report and return its saved file path.
def create_excel_report(
    output_path: str | Path | None = None,
    *,
    csv_path: str | Path = CSV_FILE,
    reviews_path: str | Path = REVIEWS_FILE,
    api_responses_path: str | Path = API_RESPONSES_FILE,
) -> Path:
    """
    Create the complete Excel report and return its saved file path.

    Optional paths make the function easy to test without changing the real
    project files. The GUI can normally call this function with no arguments.
    """

    csv_path = Path(csv_path)
    reviews_path = Path(reviews_path)
    api_responses_path = Path(api_responses_path)

    csv_rows = _read_csv_rows(csv_path)
    saved_reviews = _read_json_object(reviews_path)
    api_responses = _read_json_object(api_responses_path)
    final_rows = _prepare_final_rows(csv_rows, saved_reviews)

    if output_path is None:
        RESULTS_FOLDER.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = (
            RESULTS_FOLDER
            / f"SK_BrickRescue_Batch_Report_{timestamp}.xlsx"
        )
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    _add_dashboard(workbook, final_rows)
    _add_batch_results(workbook, final_rows)
    _add_manual_reviews(workbook, saved_reviews)
    _add_api_audit(workbook, api_responses)
    _add_instructions(workbook)

    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.calculation.calcMode = "auto"
    workbook.save(output_path)

    return output_path


if __name__ == "__main__":
    created_file = create_excel_report()
    print(f"Excel report created: {created_file}")
