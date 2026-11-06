# SK BrickRescue Part Recognition

SK BrickRescue Part Recognition is a Windows desktop application for identifying
photographed LEGO parts, reviewing the suggested part and color, rescanning a
part from another angle, and creating CSV, JSON, and Excel records for each
batch.

This package contains the complete application source code, required branded
assets, dependency list, and Windows setup/start helpers. The `images` and
`results` folders begin empty so each employee starts with a clean workspace.

## Before You Begin

You need:

- A Windows 10 or Windows 11 computer.
- Internet access while installing packages and while identifying parts.
- Python 3.11 or newer from <https://www.python.org/downloads/>.
- The **Add python.exe to PATH** option selected during Python installation.
- A camera only if you want to use **Take Photo Now**. Uploading an existing
  rescan photo does not require a camera.
- Microsoft Excel, LibreOffice Calc, or another `.xlsx` viewer if you want to
  open exported Excel reports.

Brickognize and BrickLink are online services. Part recognition and live
catalog lookups require those services to be reachable.

## Recommended Windows Setup

1. Extract the entire ZIP file to a normal folder. Do not run the application
   from inside the ZIP preview.
2. Open the extracted `SK_BrickRescue_Part_Recognition` folder.
3. Double-click `SETUP_WINDOWS.bat`.
4. Wait for **Setup completed successfully**.
5. Double-click `START_APPLICATION.bat` whenever you want to open the program.

The setup file creates a private `.venv` environment inside the project and
installs everything listed in `requirements.txt`. It does not change the
application source code or results.

### First-Run Checklist

Before processing a real LEGO set, confirm all of the following:

- The ZIP has been fully extracted.
- The project folder has not been separated from its `assets`, `src`,
  `images`, or `results` subfolders.
- Python 3.11 or newer is installed with the Python launcher available.
- `SETUP_WINDOWS.bat` finished with **Setup completed successfully**.
- `START_APPLICATION.bat` opens the branded application window.
- Internet access is available for Brickognize and BrickLink.
- Windows camera permission is enabled if camera capture will be used.
- A small practice image can be processed and appears in the results list.
- The green Excel icon becomes available only after processing is complete.
- **Open Results Folder** opens the expected project `results` folder.

After this check, clear the practice data or finish/archive the practice batch
before beginning production work.

Windows may show a security prompt for downloaded batch files. Confirm that
the file came from this SK BrickRescue package, then choose **More info > Run
anyway** if company policy permits it.

## Manual Setup in PowerShell

Use this only if the setup file is blocked or if you prefer terminal commands.
Open PowerShell in the extracted project folder and run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe gallery_interface\gallery_gui.py
```

For later launches, only the final command is required. The application can
also be started by double-clicking `START_APPLICATION.bat`.

## What Gets Installed

`requirements.txt` installs:

- `requests` for Brickognize and BrickLink web requests.
- `Pillow` for displaying images and fallback color estimation.
- `customtkinter` for the desktop interface.
- `openpyxl` for Excel reports.
- `opencv-python` for live camera capture.

Do not install these packages one at a time unless troubleshooting. Installing
from `requirements.txt` prevents a required package from being missed.

## Normal Workflow

1. Open the application.
2. Select **Select LEGO Images** and choose one or more JPG, JPEG, or PNG files.
3. Select **Process All Images**.
4. Select a filename in the left list to view its uploaded photograph,
   BrickLink catalog reference, prediction, confidence, and review status.
5. If desired, choose and save a color or select **Rescan Part**. These options
   are always available after a result is processed and are never mandatory.
6. Export Excel with the green spreadsheet icon beside **Open Results Folder**
   or with **Reports > Export Excel Report**. Export becomes available only
   when results are loaded and processing is finished.
7. Select **Start New Batch** when the current LEGO set is complete.

The status symbols beside selected images mean:

- **✓ green:** processed and ready.
- **⚠ yellow:** an optional part or color check is suggested.
- **↻ blue:** that filename already has a saved CSV row and will be reused.
- **… blue:** currently processing.
- **✕ red:** processing failed.

Hover over buttons, counters, labels, status symbols, and images for additional
tooltips. The **Help > Workflow Guide** window also explains every available
route inside the application.

## Important Operating Rules

These rules protect saved records and prevent the application from appearing
to run the wrong batch:

1. Always extract the ZIP before setup or use. Do not launch files from the ZIP
   preview, an email preview, or a temporary download view.
2. Keep the complete project folder together. The Python files locate assets
   and results relative to that folder.
3. Run `SETUP_WINDOWS.bat` before the first launch on each computer.
4. Use `START_APPLICATION.bat` for normal operation. Do not double-click an
   individual `.py` file unless intentionally developing or troubleshooting.
5. Do not open `src/main.py` to launch the desktop interface. It is the backend;
   the interface entry point is `gallery_interface/gallery_gui.py`.
6. Wait for processing to finish before exporting Excel, starting another
   batch, closing the application, or changing files in `results/`.
7. A faded spreadsheet icon is intentionally disabled. Do not treat it as an
   error; it activates when results exist and no processing is running.
8. Select the correct result in the left list before saving a color, rescanning,
   opening BrickLink, or comparing images.
9. Color verification and part rescanning are optional for every processed
   result. A yellow suggestion recommends attention but does not force a save.
10. Confirm the visible **✓ Saved** or `Last saved` message after any manual
    decision, camera photo, comparison choice, Excel export, or batch archive.
11. Do not manually rename, move, or edit files in `results/` while the
    application is open.
12. Do not delete active CSV or JSON files merely to clear the screen. Use the
    batch controls so the application keeps its in-memory state synchronized.
13. Use **Clear Screen Only** only when continuing the same LEGO set and saved
    dataset.
14. Use **Finish & Start New LEGO Set** when moving to a separate set. This
    archives the current files and prevents the next set from continuing in the
    wrong dataset.
15. Give completed batches descriptive names. The timestamp prevents two
    batches with similar names from overwriting one another.
16. Use unique source filenames within a batch. A blue **↻** means the filename
    matches an existing saved record and will be restored instead of uploaded
    to Brickognize again.
17. If a genuinely new image has the same filename as an earlier image, rename
    it before selecting it or begin a separate archived batch.
18. Do not place unrelated loose image files in the project root. Select images
    from their existing location or use the `images/` folder for an intentional
    command-line workflow.
19. Camera captures are managed automatically. Do not pre-create or manually
    renumber `lego_XX.jpg` files in `results/captured_images/` during a batch.
20. Image previews preserve the full photo and do not crop it. Prepare or crop
    source photography in the separate image-preparation workflow if needed.
21. The application does not delete the original selected photographs. Keep
    the originals until the batch and exported report have been checked.
22. Treat CSV and JSON files as application records. If manual spreadsheet
    analysis is needed, work from a copy or from the exported Excel report.
23. Closing an exported workbook does not remove it. Excel files remain in the
    active results folder or completed-batch archive.
24. Back up the project folder regularly if results are operationally
    important. Completed batches are separated locally but are not an external
    backup by themselves.

## Optional Color Verification

Color verification is available for every processed result, including
high-confidence results.

1. Select a processed image.
2. Choose a color from the color list.
3. Review the updated BrickLink catalog image.
4. Select **Save Color**.
5. Confirm the **✓ Saved** message and the `Last saved` message in the toolbar.

The application keeps the original prediction and the final employee decision
in separate output fields. A saved color can therefore be marked as confirmed
or corrected without losing the original API result.

When BrickLink is available, the color list is limited to colors cataloged for
that part. If the lookup is unavailable, the application uses its built-in
review color list so work can continue.

## Optional Part Rescan

1. Select any processed result.
2. Select **Rescan Part**.
3. Choose **Take Photo Now** or **Upload Existing Photo**.
4. For a camera photo, capture the part, review the preview, and choose
   **Use This Photo**. Use **Retake** if needed.
5. Compare the original and alternate predictions.
6. Choose which result to save.

Camera photographs are saved automatically in
`results/captured_images/` using the next available `lego_XX.jpg` name for the
active batch. The comparison decision updates the saved result for that part.
Saved confirmations appear beside the rescan control and in the toolbar.

If the camera does not open:

- Close other programs that may be using it.
- Confirm Windows camera permission for desktop applications.
- Use **Upload Existing Photo** to continue without the camera.

## Starting a New Batch

**Start New Batch** provides two non-destructive choices:

- **Clear Screen Only** removes the current selections from the screen while
  keeping the same active result files. Use this to continue the same LEGO set.
- **Finish & Start New LEGO Set** asks for a name, archives the current saved
  files under `results/completed_batches/`, and opens a clean independent batch.

The archive folder name combines the entered batch name with a timestamp, so a
later batch cannot overwrite an earlier completed batch.

## Saved Files

All generated files stay inside the extracted project folder. The distributed
package starts with empty `images/` and `results/` folders.

```text
results/
├── captured_images/                 Camera rescan photographs
├── completed_batches/               Archived LEGO sets
├── brickognize_results.csv          Current summarized results
├── brickognize_responses.json       Complete API responses
├── brickognize_reviews.json         Saved employee decisions
├── part_color_cache.json            Previously loaded part/color options
└── SK_BrickRescue_Batch_Report_*.xlsx
```

The application does not delete the original images selected from the
computer. Clearing the screen also does not delete saved results.

### How Saving and Updating Work

- A successful new recognition appends or updates the current CSV record and
  stores the complete API response in JSON.
- Saving a color updates the employee review JSON and the matching final fields
  in the CSV. It does not erase the original predicted values.
- Saving a rescan comparison updates the selected part's accepted result. A
  camera capture is also saved as an image in the active batch.
- Excel export reads the current CSV and JSON files at the moment the report is
  created. Later color or rescan changes do not rewrite an older `.xlsx` file;
  export a new Excel report when an up-to-date workbook is needed.
- CSV and JSON are the active application data. Excel is a dated report created
  from that data.
- **Clear Screen Only** changes the visible queue but continues writing to the
  same active CSV and JSON files.
- **Finish & Start New LEGO Set** moves the active saved data into a unique
  completed-batch folder and creates a clean active workspace.
- Camera filenames use the next available sequential number for the active
  batch. Unrelated images elsewhere do not change that sequence.

Important Excel/CSV fields include:

| Field | Meaning |
| --- | --- |
| Part ID, Part Name, Part Confidence | Original Brickognize part prediction. |
| Predicted LEGO Color | Original Brickognize color prediction. |
| Predicted BrickLink Color ID | BrickLink identifier for the predicted color. |
| Color Confidence | Confidence for the automatic color prediction. |
| Final Part ID, Final Part Name | Part accepted after any optional rescan. |
| Final LEGO Color | Color accepted after optional verification. |
| Final BrickLink Color ID | BrickLink identifier for the accepted color. |
| Part Approval Status | Auto-Accepted, Pending Review, Confirmed, or Corrected. |
| Color Approval Status | Auto-Accepted, Pending Review, Confirmed, or Corrected. |
| Final Review Status | Overall operational status for the row. |

Color IDs are catalog identifiers, not confidence percentages.

## Buttons and Menus

- **Select LEGO Images:** adds photographs to the active queue.
- **Process All Images:** processes new files and restores matching saved rows.
- **× beside Selected Images:** removes only the selected queue item.
- **Save Color:** stores the selected color for the active result.
- **Rescan Part:** opens the camera/upload comparison workflow.
- **Spreadsheet icon:** creates and opens an Excel report when enabled.
- **Open Results Folder:** opens the folder containing current outputs.
- **Open BrickLink:** opens the selected part and color in the catalog.
- **Start New Batch:** clears the view or archives and starts another LEGO set.
- **File menu:** image selection, folder access, queue removal, clearing, exit.
- **Batch menu:** processing and new-batch actions.
- **Reports menu:** Excel export.
- **Help menu:** Workflow Guide and application About window.

## Troubleshooting

### “Python was not found”

Install Python from python.org and select **Add python.exe to PATH**. Close and
reopen the folder before running setup again.

### PowerShell blocks activation

Activation is not required. Use the full `.venv\Scripts\python.exe` commands
shown in **Manual Setup in PowerShell**, or use the provided batch files.

### A package will not install

Confirm internet access, close the application, and run `SETUP_WINDOWS.bat`
again. The setup safely reuses the existing environment.

### Recognition fails or times out

Confirm internet access and try again. Brickognize is an external service; a
temporary service interruption can prevent identification.

### The spreadsheet icon is faded

This is intentional. It becomes green and clickable only after at least one
result is loaded and all current processing has finished.

### Excel was created but did not open

Select **Open Results Folder**. The `.xlsx` file is still saved even if Windows
does not have a default spreadsheet application.

### An image was already saved

A blue **↻** means the filename matches an existing CSV row. The application
reuses that saved result to avoid duplicate API work. Rename a genuinely new
photo or finish/archive the current LEGO set before processing it.

### The application window is too large or small

The main window and secondary dialogs automatically fit the monitor. Supported
windows may also be resized manually. Image previews always preserve the full
photo without cropping.

## Project Folder

```text
SK_BrickRescue_Part_Recognition/
├── assets/                           Logo and Windows icon
├── images/                           Optional command-line input folder
├── results/                          Generated application outputs
├── src/
│   ├── gui.py                        Desktop interface and user workflows
│   ├── main.py                       Recognition and output backend
│   ├── bricklink_colors.py           Supported review colors
│   ├── part_color_service.py         BrickLink color lookup and cache
│   ├── review_manager.py             Review persistence and CSV updates
│   └── excel_report.py               Formatted Excel report builder
├── README.md                         Setup, use, and troubleshooting guide
├── requirements.txt                  Required Python packages
├── SETUP_WINDOWS.bat                 First-time Windows setup
└── START_APPLICATION.bat             Normal Windows launcher
```

`src/main.py` is the backend. For the normal desktop experience, start
`src/gui.py` or use `START_APPLICATION.bat`.

## Editing and Customizing the Application

This package includes the complete editable Python source code. It is not a
locked executable. Lana or another developer may open the entire extracted
folder in Visual Studio Code or another Python editor and change the interface,
instructions, recognition workflow, review rules, confidence thresholds,
reports, colors, logos, filenames, or other application behavior.

The two Windows batch files continue working after normal source-code changes:

- `SETUP_WINDOWS.bat` creates the private Python environment and installs the
  packages in `requirements.txt`. It normally runs only once. Run it again
  after adding or changing a package in `requirements.txt`, when setting up a
  different computer, or when rebuilding the `.venv` folder.
- `START_APPLICATION.bat` always opens the current saved version of
  `src/gui.py`. Close and reopen the application after editing so the latest
  changes are loaded. Normal edits do not require setup to be rerun.

The launch files need to be updated only if `src/gui.py`, `requirements.txt`,
or the `.venv` folder is renamed or moved. The whole project folder itself may
be moved to another location as long as its internal folder structure remains
together.

Before making substantial changes, create a backup copy of the extracted
project folder. Do not edit generated files in `results/` as a substitute for
changing the application logic; those files contain employee batch data.

### Safe Change Procedure

1. Close the application.
2. Copy the entire project folder to create a recoverable backup.
3. Open the copied working folder in Visual Studio Code or another editor.
4. Change only the intended source or asset files.
5. Save the files without renaming their extensions.
6. If `requirements.txt` changed, run `SETUP_WINDOWS.bat` again. Otherwise,
   setup does not need to be repeated.
7. Open the updated code with `START_APPLICATION.bat`.
8. Test image selection, processing, color saving, rescan saving, Excel export,
   and new-batch archiving with practice data.
9. Confirm `images/` and `results/` are clean before distributing a fresh copy.

Normal edits to `src/gui.py`, `src/main.py`, `src/excel_report.py`,
`src/review_manager.py`, `src/part_color_service.py`,
`src/bricklink_colors.py`, the README, logo, or icon do not stop the batch files
from working. Changes take effect the next time the application is opened.

### Files and Names to Keep Stable

Unless the launch code is also deliberately updated, do not rename or relocate:

- `src/gui.py`
- `src/main.py`
- `requirements.txt`
- `assets/sk_brickrescue_logo.png`
- `assets/sk_brickrescue.ico`
- the `.venv` folder created by setup
- the internal `src`, `assets`, `images`, and `results` folder structure

If the entire project folder is moved, move it as one complete folder. The
project's outer folder name may change without affecting normal operation.

### When Setup Must Be Run Again

Run `SETUP_WINDOWS.bat` again when:

- installing on a different computer;
- the `.venv` folder was deleted, damaged, or intentionally rebuilt;
- a package was added to or changed in `requirements.txt`; or
- Python was reinstalled and the existing environment no longer works.

Setup does **not** need to be rerun after ordinary interface, instruction,
workflow, color, report-layout, or comment changes.

### Preparing a Fresh Copy for Someone Else

1. Close the application.
2. Preserve any needed batch results somewhere outside the distribution copy.
3. Empty `images/` and `results/` in the copy being distributed.
4. Do not include `.venv`, `__pycache__`, `.pyc`, test output, sample results,
   personal photos, editor settings, or development notes.
5. Keep the README, requirements, batch files, assets, and all six source files.
6. ZIP the complete `SK_BrickRescue_Part_Recognition` folder so empty `images`
   and `results` folders remain present.
7. Test the ZIP by extracting it to a different folder and following the
   first-run instructions.

## Maintenance Notes

- File paths are resolved from the project folder, not the terminal's current
  directory.
- Network work runs outside Tkinter's main thread where appropriate.
- Original API predictions remain available after employee corrections.
- Brickognize currently uses an internal API endpoint that may change outside
  SK BrickRescue's control.
- Pillow-based color estimation remains available when Brickognize identifies
  a part but omits a color prediction.
- Source files include module, function, workflow, and decision comments to
  support future maintenance without changing normal operation.
- Each source module begins with a `# MAINTAINER MAP` describing its ownership,
  data flow, cross-file relationships, and rules that must remain true. Targeted
  `#` comments also explain threading, queues, saving, fallbacks, batch
  separation, camera captures, and Excel snapshot behavior beside the relevant
  code.
- Every class and function-including internal helpers and nested GUI callbacks-
  has a nearby `# CLASS GUIDE` or `# FUNCTION GUIDE` stating its purpose. The
  function's docstring directly below provides the Python-level explanation.

Developed for SK BrickRescue by Jennifer Shapiro.
