@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo SK BrickRescue Part Recognition - First-Time Setup
echo ============================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo Python was not found.
    echo Install Python 3.11 or newer from https://www.python.org/downloads/
    echo During installation, select "Add python.exe to PATH".
    echo Then close this window and run SETUP_WINDOWS.bat again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the private Python environment...
    py -3 -m venv .venv
    if errorlevel 1 goto :failed
)

echo Updating the package installer...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed

echo Installing the application requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Setup completed successfully.
echo You may now double-click START_APPLICATION.bat.
pause
exit /b 0

:failed
echo.
echo Setup did not finish. Check the message above and the README file.
pause
exit /b 1
