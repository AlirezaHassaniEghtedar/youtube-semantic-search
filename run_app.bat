```bat
@echo off
setlocal

cd /d "%~dp0"

echo ========================================
echo Updating yt-dlp...
echo ========================================
echo.

.\venv\Scripts\python.exe -m pip install --upgrade yt-dlp

if errorlevel 1 (
    echo.
    echo ERROR: Failed to update yt-dlp.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Checking installed yt-dlp version...
echo ========================================
.\venv\Scripts\python.exe -c "import yt_dlp, sys; print('Python:', sys.executable); print('yt-dlp:', yt_dlp.version.__version__)"

if errorlevel 1 (
    echo.
    echo ERROR: Could not import yt-dlp.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Starting application...
echo ========================================
echo.

.\venv\Scripts\python.exe run_app.py

pause
```
