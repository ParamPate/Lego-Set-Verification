# Developer Handoff

This document is the maintenance reference for the SK Brickrescue LEGO
Verification application. Start with the root [`README.md`](../README.md) for
installation and operator instructions. Read this file before changing
inventory math, imports, variation allocation, mappings, persistence, or
exports.

## 1. Delivery snapshot

- Application: Windows Python/Tkinter desktop application
- Delivery format: source code; the client intends to maintain it
- Snapshot date: 2026-08-13
- Verified runtime: Python 3.14.2
- Automated tests: 122 tests in 17 files, all passing on 2026-08-13
- Launch command: `python tkinter_app.py`
- Full test command: `python -m unittest discover -s tests`

No packaging or installer is required for this handoff. Do not commit generated
reports, saved jobs, API keys, or local caches.

### Delivery check before sending the project

Run `git status --short` and review every local file. At this snapshot:

- `data/brickognize_results/` is untracked, but
  `tests/test_export_dialog_defaults.py` references its sample CSV files. Include
  that directory in the delivered source bundle if the client should be able to
  run the suite unchanged, or convert those files into tracked test fixtures.
- `logo.png` is an unused, untracked experiment and is not needed by the app.
- This handoff rename and the README cleanup may be uncommitted unless the
  maintainer explicitly commits them.

## 2. What the application does

The application compares a reviewed physical-part count with a Rebrickable set
inventory and produces an operator verification report. The typical data flow
is:

```text
Rebrickable set inventory
          +
manual Have values or reviewed Brickognize CSV files
          |
part/color mapping and optional variation allocation
          |
comparison: Complete, Missing, Extra, or Not in set
          |
operator review and surplus-removal checklist
          |
CSV attention report or missing-only BrickLink Wanted List XML
```

The UI is intentionally operator-driven. Importing or editing inventory does
not silently run another comparison.

## 3. Repository guide

| Path | Responsibility |
| --- | --- |
| `tkinter_app.py` | Windows UI, workflow state, background work, dialogs, table editing, import orchestration, exports |
| `src/api_rebrickable.py` | Rebrickable requests, response normalization, external IDs, catalog data |
| `src/api_credentials.py` | User-scoped API-key storage and validation |
| `src/app_settings.py` | Persistent application settings |
| `src/bricklink_mapping.py` | BrickLink/Rebrickable part mapping, cache statuses, ambiguity handling |
| `src/user_input.py` | Reviewed CSV validation, filtering, aggregation, and import summaries |
| `src/relationships.py` | Part relationship data and variation/alternative enrichment |
| `src/comparison.py` | Comparison math and Wanted List row selection/XML generation |
| `src/storage.py` | Saved-job persistence and atomic JSON writes |
| `data/part_relationships.csv` | Cached Rebrickable relationships |
| `data/color_map.csv` | BrickLink-to-Rebrickable color reference data |
| `data/part_map.csv` | Generated part-number mapping cache and manual selections |
| `tests/` | Unit and focused UI-logic regression tests |

## 4. Non-negotiable inventory rules

These are business rules, not incidental implementation details.

### 4.1 Identity and color mapping

- Rebrickable IDs are the internal comparison keys when a confirmed mapping
  exists. BrickLink IDs are retained for operator display and purchasing.
- Never guess that an arbitrary BrickLink ID is also a Rebrickable ID.
- If a BrickLink color has no Rebrickable association, preserve the row with its
  BrickLink color ID. It must appear in comparison output as an attention row;
  it must not disappear because of a null grouping key.
- The UI labels an unmapped color as `No RB match`. Such a row cannot be safely
  exported to BrickLink XML until its part and color identities are confirmed.

### 4.2 Part mapping and API failures

- A transient Rebrickable failure must not be cached as a permanent mapping or
  permanent miss. Let the error reach the caller and leave the cache unchanged
  so a later run can retry.
- Legacy `ASSUMED_MATCH` and unresolved cache rows are retried rather than
  treated as authoritative.
- When Rebrickable provides several BrickLink IDs, preserve every candidate.
  Do not choose the first ID silently.
- The **Review BrickLink Part Mappings** dialog opens each candidate in the
  BrickLink catalog and stores an explicit manual selection when the operator
  chooses one.
- An ambiguous or unresolved part remains visible to the operator and is
  excluded from XML.

Current mapping statuses include direct/confirmed mappings, manual selections,
ambiguous external IDs, assumed IDs only when no external ID exists, and
unresolved rows. Before changing this format, inspect both
`src/bricklink_mapping.py` and `tests/test_bricklink_mapping_cache.py`.

### 4.3 Reviewed CSV imports

The importer accepts only rows where:

- `Final Review Status` is `Ready` (case-insensitive after trimming);
- `Type` is `part` or blank; and
- `Final Part ID` and `Final BrickLink Color ID` are present.

One CSV row represents one physical item. Quantity is the number of identical
part/color rows.

More than one CSV can be imported into a job. Every additional import receives
an impact preview and one of these atomic choices:

- **Add as another scan batch**: add all incoming quantities to existing Have.
- **Fill blanks only**: use incoming quantities only where current Have is zero
  or blank; preserve nonzero Have values.
- **Cancel**: change nothing, including variations, report state, and import
  history.

Exact duplicate files are detected before acceptance. Cancelling the variation
review cancels the entire import. The system intentionally has no **Replace
previous import** mode; add it only after defining how manually edited Have
values and previous variation allocations should be distinguished from imported
quantities.

The important re-import invariant is that direct-match rows unrelated to a
variation proposal must not be lost when an import baseline is replayed.

### 4.4 Variations and alternatives

- Only same-color variation or alternative relationships can satisfy a target
  shortage.
- Wrong-color relationships are display/enrichment information only. They may
  explain a surplus but cannot be allocated as substitutes.
- Allocations are operator-entered whole numbers.
- An allocation cannot exceed the source quantity on hand.
- An allocation cannot exceed the target's remaining shortage.
- One shared source quantity cannot be double-allocated across targets.
- Any unallocated source quantity remains on the source row.
- Validate the complete allocation set before mutating any quantity.
- Persist both the accepted proposal and the untouched import baseline so
  re-review and resume can replay the proposal without cumulative drift.

Saved jobs written before allocation-state persistence must continue to load.
Treat new saved-job fields as backward-compatible additions with safe defaults.

### 4.5 Comparison and exports

- The complete comparison is the source of truth.
- The visible Verification Report is attention-only: Missing, Extra, and Not in
  set rows.
- CSV export is an attention-only copy of the Verification Report.
- BrickLink XML is a purchase list and contains only rows with
  `quantity_missing > 0` and confirmed BrickLink part and color IDs.
- XML `MINQTY` is the remaining missing quantity, not the full set requirement.
- XML excludes owned quantities, Complete rows, Extra rows, Not-in-set rows,
  and unresolved mappings.
- XML uses `ITEMTYPE=P`, `CONDITION=U`, and the optional Bag / XML Remark. It
  does not include a set ID or `QTYFILLED`.
- Keep the XML shape accepted by BrickLink: no XML declaration and an
  `INVENTORY` root.

### 4.6 Workflow state

- Editing Have, importing data, changing variations, or starting a new set
  invalidates the current report.
- Invalidation clears report state and selects Required Parts. It does not run
  Compare automatically.
- **Finish Verification** is allowed only after all physical surplus rows have
  been marked Removed in the removal checklist.
- Completed jobs are read-only until explicitly reopened.
- While `self.busy` is true, quantity edits and variation review are blocked.
  The status text and busy cursor explain that a background operation is still
  running.

## 5. Persistence and generated data

### API credentials

The Rebrickable key is stored outside the repository at:

```text
%LOCALAPPDATA%\SK Brickrescue\LEGO Set Verification\.env
```

Never move this credential into source-controlled files.

### Saved jobs

`saved_jobs/*.json` stores the active set, editable quantities, accepted import
history, variation state, report snapshots, remarks, removal checklist, and
workflow stage. Job writes use a temporary file plus atomic replacement so an
interrupted save does not destroy the only job file.

### Mapping and API caches

- `data/part_map.csv` is generated locally and also uses atomic replacement.
- `rebrickable_cache.sqlite` contains cached HTTP responses with a 24-hour
  expiration.
- Reference files can be refreshed through **Settings > Data updates...**.
- Reference updates are blocked while a verification job is active.

Atomic replacement protects the previous file from a crash during writing. It
does not provide version history; normal backups are still useful.

## 6. Tkinter and background-work constraints

Tkinter widgets must be created and changed on the main UI thread.

- Network and slower data work may run on worker threads.
- Workers return plain data or exceptions; the main thread owns dialogs and
  widget updates.
- Image workers use the image-result queue, which is drained by the main loop.
- Be careful scheduling modal dialogs from worker completion callbacks. Modal
  waits and nested test harness event loops can make thread timing misleading.
- Focused UI-logic tests are useful, but a real manual Tkinter pass remains
  necessary for focus behavior, modal ownership, scrolling, and visual layout.

The main table uses a neutral selected/unselected tab treatment. Rebrickable
identifier columns are hidden initially but remain available through the
Columns menu. Preserve that discoverability when changing table columns.

## 7. Current known limitations and deferred cleanup

These items were known at handoff and were not considered release blockers:

1. The variation dialog binds `<MouseWheel>` globally while the pointer is over
   its canvas. Closing the dialog before `<Leave>` can leave the binding aimed
   at a destroyed canvas. Add explicit cleanup in the dialog close path.
2. Part-table sorting can conflict with counterpart adjacency. Define which
   behavior wins before refactoring it.
3. Rebrickable relationship type `T` is not handled as a supported substitute.
   Confirm its business meaning before adding it.
4. Parts-table and report-table editor plumbing contains duplication and some
   known unused helpers. Refactor only with focused Escape, focus, sorting, and
   persistence tests.
5. There is no Replace mode for multiple-CSV imports, by design.
6. `CONDITION` is currently hard-coded to used (`U`) in Wanted List XML.
7. External services are mocked in much of the automated suite. The client
   should still perform a live API and catalog-link smoke test with its own key.

The earlier concern that pressing Escape in the Have editor committed the edit
was reviewed with real Tk behavior and was not reproduced. Defensive unbinding
would be reasonable cleanup, but it is not listed as an active high-priority
defect.

## 8. Verification before future releases

### Automated checks

From the repository root:

```powershell
python -m unittest discover -s tests
```

For a focused area, run its module directly, for example:

```powershell
python -m unittest tests.test_multiple_csv_import
python -m unittest tests.test_variation_allocations
python -m unittest tests.test_bricklink_mapping_cache
python -m unittest tests.test_wanted_list_progress
```

Tests cover pure business logic and selected UI workflows, including unmapped
colors, API-failure cache safety, multiple CSV imports, variation allocation,
busy guards, report invalidation, removal checks, mapping review/catalog links,
atomic persistence, and Wanted List filtering.

### Manual acceptance checklist

Use the client's Rebrickable key and keep test output separate from real jobs.

1. Launch the app and confirm API-key testing and saving.
2. Start a normal set such as `10475` and confirm the required inventory loads.
3. Import a reviewed CSV and verify the preview totals.
4. Import a second, different CSV once with **Add** and once in a separate job
   with **Fill blanks only**.
5. Attempt an exact duplicate import and confirm the warning.
6. Use a variation-bearing set/import, allocate a partial quantity, save,
   resume, and re-review the allocation without quantity drift.
7. Use set `77260` to exercise ambiguous BrickLink mapping review; confirm the
   candidate catalog links and manual choice.
8. Compare parts and verify Missing, Extra, and Not-in-set math against a small
   hand calculation.
9. Confirm an unmapped color remains visible and is excluded from XML.
10. Confirm editing/importing invalidates the report and returns to Required
    Parts without automatically comparing.
11. Confirm Finish Verification is blocked until every surplus-removal row is
    checked.
12. Save and resume an in-progress job; then complete, reopen, and edit it.
13. Export CSV and XML. Import the XML into BrickLink's Wanted List upload page
    and confirm item IDs and missing quantities before accepting the upload.
14. Close and reopen the app and confirm settings persist without exposing the
    API key.

A large-set timing pass (roughly 500+ inventory rows) is also recommended on the
client's actual computer.

## 9. Safe maintenance workflow

1. Read `README.md` and this document.
2. Run `git status --short`; do not overwrite unrelated local changes.
3. Locate the business rule and its existing tests before editing.
4. Make the smallest coherent change.
5. Add a regression test that fails for the old behavior.
6. Run the focused test module, then the full suite.
7. Manually test Tkinter behavior when focus, dialogs, threads, or layout change.
8. Update both documents if operator behavior or a non-negotiable rule changes.
9. Do not commit, push, or package unless the client explicitly requests it.

## 10. Starter prompt for Codex or ChatGPT

Copy the prompt below into Codex or ChatGPT and replace the task placeholder.
Codex can inspect a local repository directly. In ChatGPT, attach the relevant
files (at minimum this handoff, the affected source files, and tests) unless the
chat has a connected project workspace.

```text
You are maintaining the SK Brickrescue LEGO Verification Python/Tkinter
application.

Task:
<describe the requested change or bug here>

Before changing code:
1. Read README.md and docs/HANDOFF.md completely.
2. Inspect git status and preserve all unrelated or user-authored changes.
3. Inspect the affected implementation and existing tests. Do not guess about
   behavior that can be verified from the repository.
4. If you cannot access the repository directly, say so and ask me to attach
   the relevant files. Do not claim to have run code or tests you cannot run.

Non-negotiable rules:
- Preserve unmapped BrickLink colors as visible comparison rows.
- Do not cache transient API failures as mappings or permanent misses.
- Preserve all ambiguous BrickLink ID candidates until an operator selects one.
- Multiple CSV imports must be atomic and must not lose unrelated direct rows.
- Variation allocations are same-color, whole-number, source-limited,
  shortage-limited, and cannot double-spend a shared source.
- BrickLink XML is missing-only and includes only confirmed BrickLink IDs.
- Preserve backward compatibility with older saved jobs.
- Keep all Tkinter widget work on the main UI thread.

Implementation expectations:
- Make a narrow, maintainable change; avoid unrelated architecture rewrites.
- Add or update a regression test for the exact behavior.
- Run the focused tests and then:
  python -m unittest discover -s tests
- Report what changed, test results, and any manual UI checks still needed.
- Do not commit, push, package, delete data, or expose API keys unless I ask.
```

For the best result, append an example input, the expected behavior, and any
error message or screenshot to the task description.
