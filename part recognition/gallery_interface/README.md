# Step 2 Gallery Interface

Run `gallery_gui.py` with the existing Step 2 virtual environment. The
combined `lego_workflow.py` launcher now opens this version automatically.

The gallery reuses the classic recognition, review, CSV, color, rescan, and
Excel backends. It shows vertically scrollable source/reference image pairs on
the left and the selected result's actions and identification details on the
right.

The pre-gallery interface remains available at:

`../src/gui_classic_backup_20260831.py`

## Drop a folder and analyze

Drop one folder onto the banner at the top of the recognition window. Recognition
starts automatically for JPG, JPEG, and PNG part photos, including subfolders.
**Choose folder & analyze** does the same thing without drag and drop. No Process
All or Next click is needed. These inputs should contain one cropped part per
photo; use Step 1's **Analyze all photos** first for photographs of many pieces.

The banner states that recognition uploads the selected photos to
`api.brickognize.com`. Each drop creates a fresh `recognition_*` folder beside
the source photos, preserving previous results and preventing reused filenames
from another set from supplying the wrong predictions. Generated recognition
outputs and detection diagnostics are excluded from source discovery. Duplicate
photo filenames inside different subfolders must be renamed before analysis.

**Results Folder**, reviews, and Excel export follow the dropped batch. The
combined launcher's inventory handoff follows it too. A new drop is refused while
recognition or a rescan is running; failed pending saves also block switching.

Native folder drop uses `tkinterdnd2`. It is installed in this Mac's `.venv-mac`;
on another computer, install the updated requirements or rerun Windows setup.
If the native component cannot load, the folder picker remains available.

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
