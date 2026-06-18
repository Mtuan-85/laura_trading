@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [run_gui] ERROR: venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

python -m app.main
if errorlevel 1 (
    echo.
    echo [run_gui] App exited with error code %errorlevel%.
    pause
)
