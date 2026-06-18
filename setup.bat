@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\" (
    echo [setup] Creating virtual env...
    python -m venv .venv
    if errorlevel 1 (
        echo [setup] ERROR: failed to create venv. Is Python on PATH?
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

echo [setup] Upgrading pip...
python -m pip install --upgrade pip

echo [setup] Installing requirements...
pip install -r requirements.txt
if errorlevel 1 (
    echo [setup] ERROR: pip install failed.
    pause
    exit /b 1
)

echo [setup] Installing patchright browsers...
python -m patchright install chromium

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [setup] WARNING: ffmpeg not on PATH. Install it before running the app.
) else (
    echo [setup] ffmpeg OK.
)

echo.
echo [setup] Done. Next steps:
echo   1. Run launch_brave.bat (keep it running, login to grok.com/imagine).
echo   2. Run run_gui.bat.
echo.
pause
