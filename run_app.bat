@echo off
setlocal
cd /d "%~dp0"

where deno >nul 2>&1
if errorlevel 1 (
    echo.
    echo ========================================
    echo ERROR: Deno is not installed
    echo ========================================
    echo.
    echo Install Deno and add it to PATH. yt-dlp needs it to solve
    echo YouTube JavaScript challenges. See README.md.
    echo.
    pause
    exit /b 1
)

echo Deno:
deno --version
echo.
echo Make sure the bgutil PO-token provider is running on
echo http://127.0.0.1:4416 before adding channels. See README.md.
echo.

if exist "%~dp0venv\Scripts\python.exe" (
    set "PY=%~dp0venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo Updating yt-dlp...
"%PY%" -m pip install --upgrade yt-dlp
if errorlevel 1 (
    echo ERROR: Failed to update yt-dlp.
    pause
    exit /b 1
)

echo.
"%PY%" run_app.py
pause
