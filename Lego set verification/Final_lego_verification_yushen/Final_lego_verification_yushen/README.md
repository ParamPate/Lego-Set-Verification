# SK Brickrescue LEGO Verification

A Windows Tkinter application for comparing scanned LEGO parts with an official
Rebrickable set inventory. It helps an operator count parts, review valid
variations, identify missing or surplus pieces, save progress, and export a
BrickLink Wanted List for the remaining missing quantities.

This repository is delivered as source code so it can be maintained and
extended by the client. Developer details and maintenance rules are in
[`docs/HANDOFF.md`](docs/HANDOFF.md).

## Quick start

Requirements:

- Windows with a recent Python 3 installation that includes Tkinter
- A Rebrickable API key
- Internet access for Rebrickable lookups and catalog images

Install and run:

```powershell
python -m pip install -r requirements.txt
python tkinter_app.py
```

The verified development environment uses Python 3.14.2. On the original
development computer, `python` on `PATH` is a Microsoft Store alias; use the
installed interpreter directly if that alias does not run Python.

On first launch, enter the Rebrickable API key and select **Test and Save**.
The key is stored for the current Windows user at:

```text
%LOCALAPPDATA%\SK Brickrescue\LEGO Set Verification\.env
```

The key can later be replaced or removed through **Settings > Rebrickable API
Key...**. Do not commit API keys or include them in screenshots and logs.

## Operator workflow

1. Select **File > Start New Set...** and enter a Rebrickable set number.
   Entering `10475` is equivalent to `10475-1`.
2. Review the Required Parts table. Rebrickable IDs are available through the
   Columns menu but BrickLink identifiers are shown first because they are used
   for purchasing.
3. Enter `Have` quantities manually or select **File > Prefill Brickognize
   CSV...**.
4. If variation or alternative parts can fill a required same-color shortage,
   review and allocate whole-number quantities in the variation window.
5. Select **Compare Parts**.
6. Review all Missing, Extra, and Not-in-set rows in Verification Report.
7. Add optional **Bag / XML Remark** values to missing rows.
8. Remove surplus pieces from the physical table and mark every row in the
   **Extra Parts Removal Checklist** as Removed.
9. Select **Finish Verification** when the report and removal checklist are
   complete.
10. Select **File > Export Report...** and choose XML or CSV.

Changing quantities, importing another CSV, changing variations, or starting a
new set invalidates a stale report and returns the app to Required Parts. The
operator must select **Compare Parts** again; the report is never regenerated
silently.

## Importing reviewed Brickognize CSV files

The importer uses the reviewed fields rather than raw recognition predictions:

- `Final Review Status` must be `Ready`.
- `Type` must be `part` or blank.
- `Final Part ID` supplies the BrickLink part number.
- `Final BrickLink Color ID` supplies the BrickLink color.
- Quantity is determined by counting rows with the same part and color because
  each row represents one photographed part.

More than one CSV can be imported into the same job. For every additional
file, the app shows an impact preview and asks how to merge it:

- **Add as another scan batch** adds all incoming quantities. Use this for a
  different batch of photographed pieces.
- **Fill blanks only** fills zero or missing `Have` values and preserves every
  existing nonzero value.
- **Cancel** leaves quantities, variation choices, report state, and import
  history unchanged.

An exact duplicate file produces a warning before any changes are accepted.
Closing or cancelling the variation review also cancels that CSV import.

## Variation and alternative parts

Only same-color mold variations and alternatives are offered as substitutes.
The operator chooses whole-number allocations; the app prevents:

- allocating more pieces than the source quantity on hand;
- allocating more than a required target's shortage; and
- assigning the same shared source quantity to multiple targets twice.

Unallocated pieces remain on their original source row. Choices can be changed
with **Review variation allocations** and are restored when the job resumes.
Wrong-color variations remain surplus by design and may receive an explanatory
note; they are not offered as substitutes.

## BrickLink mapping review

Rebrickable occasionally supplies more than one BrickLink ID for one part. The
app never chooses the first candidate silently. Instead, **Review BrickLink Part
Mappings** lets the operator:

- open each candidate in the BrickLink catalog;
- save the correct ID as a manual mapping; or
- leave the part unresolved.

The review opens automatically when an ambiguity is found and can be reopened
through **File > Review BrickLink Mappings...**. Unresolved ambiguous parts are
identified in the report and excluded from XML. Set `77260` is a known example:
Rebrickable part `970c28` has BrickLink candidates `970c00` and `970c07`.

## Reports and saved jobs

The Verification Report contains every row that needs attention:

- **Missing** — the set requires more than the operator has;
- **Extra** — the operator has more than the set requires; and
- **Not in set** — the scanned part/color is not required by the set.

XML export is a BrickLink Wanted List, not a copy of the visible report. It
contains only missing rows that have confirmed BrickLink part and color IDs:

- `MINQTY` is the remaining missing quantity;
- `CONDITION` is `U`;
- `REMARKS` contains the optional Bag / XML Remark; and
- owned, Complete, Extra, Not-in-set, and unresolved rows are excluded.

CSV export remains an attention-only copy of the Verification Report.

**Save Progress** stores the editable quantities, imports, variation choices,
remarks, removal checklist, workflow stage, and report snapshots. In-progress
and completed jobs can be resumed through **File > Resume Saved Job**.
Completed jobs are read-only until **Reopen Verification** is selected.

## Data and generated files

| Path | Purpose | Source controlled? |
| --- | --- | --- |
| `data/part_relationships.csv` | Rebrickable part relationships | Yes |
| `data/color_map.csv` | BrickLink/Rebrickable color mapping | Yes |
| `data/part_map.csv` | Local part-number mapping cache and manual choices | No |
| `saved_jobs/*.json` | Saved verification jobs | No |
| `reports/*.csv`, `reports/*.xml` | Exported reports | No |
| `rebrickable_cache.sqlite` | 24-hour API response cache | No |
| `%LOCALAPPDATA%\...\.env` | Current user's API key | No |

Use **Settings > Data updates...** to refresh relationship and color reference
data. Automatic weekly checks are enabled by default and can be disabled there.
Reference-data updates are unavailable while a verification job is active.

## Development and tests

Run the complete regression suite from the repository root:

```powershell
python -m unittest discover -s tests
```

The current suite contains 122 tests across 17 files. The last verified run on
2026-08-13 passed all 122 tests with Python 3.14.2.

For architecture, invariants, known limitations, manual acceptance checks, and
a ready-to-paste Codex/ChatGPT maintenance prompt, read
[`docs/HANDOFF.md`](docs/HANDOFF.md).
