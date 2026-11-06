# Combined LEGO verification workflow

Run the three existing programs from one guided window:

```powershell
python .\combined_workflow\lego_workflow.py
```

The workflow keeps the original programs and their data formats intact:

1. **Image analysis** opens the adjustable `manual_contour_tool/app.py`. False boxes can be selected and removed before saving. Brickognize-ready crops are stored alone in `images*`; diagnostics are stored separately in `analysis*`. The controller records the saved crop folder for Step 2.
2. **Part and color review** opens the existing SK BrickRescue app. Use **Upload Images** for each generated `images*` folder. Repeated selections append to the active batch, and the folder identifier in each filename keeps CSV rows unique.
3. **Set inventory comparison** exports complete Ready part rows from `brickognize_results.csv` into the inventory app's normal import directory under a timestamped name, then opens the existing inventory app. Repeated pieces keep separate rows so inventory quantities remain correct. When unresolved rows exist, the prompt defaults to **No**: count Ready rows only. Explicitly choosing **Yes** includes unapproved predictions with usable identifiers in the handoff copy; the original CSV stays unchanged.

Use **Back** and **Next** in the controller to move between steps. Closing a child app returns you to the controller. The original scripts are not modified.

## Running on this Mac

The desktop tools use Tkinter, which also runs on macOS. This checkout now has a
separate `.venv-mac` environment with Python 3.14, Tkinter, and the existing app
dependencies. From the project root, launch:

```sh
.venv-mac/bin/python combined_workflow/lego_workflow.py
```

On macOS, recognition uses the Python interpreter running the launcher; it does
not try the Windows `part recognition/.venv/Scripts/python.exe` copied with the
project. Folder and Excel-report buttons use the Mac's `open` command. The
Windows launcher and file-opening behavior remain available on Windows.

For a fresh Mac setup with Homebrew and Python 3.14:

```sh
brew install python-tk@3.14
python3.14 -m venv .venv-mac
.venv-mac/bin/python -m pip install -r "part recognition/requirements.txt" -r Final_lego_verification_yushen/Final_lego_verification_yushen/requirements.txt
```

## Dependencies

The controller itself only uses Python's standard library. Each child program still uses its existing dependencies. The part-recognition virtual environment is selected automatically when `part recognition/.venv/Scripts/python.exe` exists; otherwise the Python interpreter running the controller is used.
