# Step 2 Gallery Interface

Run `gallery_gui.py` with the existing Step 2 virtual environment. The
combined `lego_workflow.py` launcher now opens this version automatically.

The gallery reuses the classic recognition, review, CSV, color, rescan, and
Excel backends. It shows vertically scrollable source/reference image pairs on
the left and the selected result's actions and identification details on the
right.

The pre-gallery interface remains available at:

`../src/gui_classic_backup_20260831.py`

## Faster review

After upload or recognition, **Needs attention** shows uncertain predictions,
failed recognition, and incomplete identifiers. Switch to **Ready** to audit
automatically accepted pieces, or **All Images** to inspect the full batch.
Part and color thresholds remain separate at 80%. RGB fallback colors always
need human review. A missing part or color identifier cannot become Ready.

Rows are grouped by their displayed part and color, including saved corrections.
Check **Include in group approval** on matching thumbnails, then choose
**Approve selected**. **Select this page** checks eligible pieces on the current
ten-image page; uncheck odd pieces before approval. Every image keeps its own
CSV row and review history. A failed approval remains available to retry.

**Enter** approves the displayed piece and advances. Arrow keys navigate.
Shortcuts leave text fields, dropdowns, and secondary windows alone. Reference
images share a cache, and the next piece's reference is loaded ahead of time.

Choose **Save Statuses** to flush approvals, or **Complete Batch** to save and
finish. Completion reports unresolved pieces and stops if a save fails. The
inventory handoff defaults to complete Ready part rows only. Original
predictions and automatic versus human approval statuses remain separate.

See [the Windows trial checklist](WINDOWS_REVIEW_TRIAL.md) before claiming a
speed or accuracy improvement.
