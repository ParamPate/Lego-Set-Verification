# Combined LEGO verification workflow

Run the three existing programs from one guided window:

```powershell
python .\combined_workflow\lego_workflow.py
```

The workflow keeps the original programs and their data formats intact:

1. **Image analysis** opens the adjustable `manual_contour_tool/app.py`. False boxes can be selected and removed before saving. Brickognize-ready crops are stored alone in `images*`; diagnostics are stored separately in `analysis*`. The controller records the saved crop folder for Step 2.
2. **Part and color review** opens the existing SK BrickRescue app. Use **Upload Images** for each generated `images*` folder. Repeated selections append to the active batch, and the folder identifier in each filename keeps CSV rows unique.
3. **Set inventory comparison** copies the current reviewed `brickognize_results.csv` into the inventory app's normal import directory under a timestamped name, then opens the existing inventory app.

Use **Back** and **Next** in the controller to move between steps. Closing a child app returns you to the controller. The original scripts are not modified.

## Dependencies

The controller itself only uses Python's standard library. Each child program still uses its existing dependencies. The part-recognition virtual environment is selected automatically when `part recognition/.venv/Scripts/python.exe` exists; otherwise the Python interpreter running the controller is used.
