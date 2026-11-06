@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo The application has not been set up yet.
    echo Run SETUP_WINDOWS.bat first.
    pause
    exit /b 1
)

start "SK BrickRescue Part Recognition" ".venv\Scripts\pythonw.exe" "gallery_interface\gallery_gui.py"
exit /b 0
