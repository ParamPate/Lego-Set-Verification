# Windows review trial

Status: not yet performed. Automated checks do not measure operator time or
recognition accuracy on real LEGO pieces.

## Prepare a repeatable 100-piece batch

1. Keep separate copies of the original and improved applications. Back up
   existing results; use fresh result folders in both trial copies. Do not
   clear production records to run this trial.
2. Photograph the same 100 separated physical pieces with the same lighting,
   background, camera settings, and filenames. Include repeated parts, similar
   molds, and difficult colors. Establish the correct part and color for every
   image by manual inspection and catalog comparison.
3. Record the Windows/Python versions, operator, set number, and image list.
   Use the same operator and computer for both runs. For repeat trials,
   alternate the run order to reduce the effect of familiarity.
4. For the review-only comparison, copy the same initial recognition results
   into each trial copy before any human approvals. Compare end-to-end timing
   separately, starting from the same original photographs. Record whether
   results and catalog images were cached; do not mix cached and fresh timing.

## Run and record

Time detection/crop editing, recognition waiting, human review, and inventory
handoff separately. Record total elapsed time as well: stage times may overlap.
Stop after inventory comparison has loaded the reviewed quantities.

| Measure per 100 pieces | Original | Improved |
| --- | --- | --- |
| Detection and crop editing, minutes | | |
| Recognition waiting, minutes | | |
| Human review, minutes | | |
| Handoff and comparison, minutes | | |
| Total elapsed minutes | | |
| Automatically accepted part predictions | | |
| Wrong automatically accepted part predictions | | |
| Automatically accepted color predictions | | |
| Wrong automatically accepted color predictions | | |
| Pieces needing manual review | | |
| Remaining unresolved pieces | | |

Audit **every** automatically accepted part and color against the established
answers, using Ready or All Images. Count part and color errors separately; one
piece can have both. Record each error's filename, predicted and correct IDs,
confidence scores, and color method. Audit time is separate from operating time.
API confidence scores are not measured correctness rates.

## Acceptance checks

- RGB fallback colors and missing identifiers appear in Needs attention.
- Select a repeated group, exclude one thumbnail, and approve. Only selected
  rows change. Original prediction fields stay intact after saving/reopening.
- Filter or navigate quickly, then press Enter. Only the displayed identity is
  approved. Typing and arrow keys in a search field do not approve/navigate.
- A failed recognition remains visible and cannot be approved without IDs.
- In a disposable trial copy, make the result CSV read-only. Pending saves must
  remain retryable and Complete Batch must not clear the workspace on failure.
  Restore write access and verify retry succeeds.
- With one unresolved row, choose No at the handoff prompt. The import contains
  complete Ready part rows only, and their row count equals inventory quantity.
- Test on the actual Windows screen, including scaling, paging, checkboxes,
  keyboard focus, and catalog-image loading/failure.

Keep both 80% thresholds unchanged for this trial. Do not expand automatic
acceptance if any audited error is found; investigate the affected cases first.
Zero observed errors in 100 pieces is a trial result, not an accuracy guarantee.

## Automated checks

From the project root in a Python environment with both applications' existing
requirements installed:

```powershell
python -m unittest discover -s "part recognition/tests" -v
python -m unittest discover -s combined_workflow -p test_workflow.py -v
```

The tests exercise real policy, persistence, parser, importer, and extracted GUI
handlers. They do not start the desktop interface or call recognition services.
