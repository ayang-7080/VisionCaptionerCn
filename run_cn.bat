@echo off
setlocal

REM Chinese UI launcher: applies the launcher.py translation overlay, no source code changes.
REM Use run.bat to start the original English UI instead.
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

title Launching VisionCaptioner (Chinese UI)...

echo.
echo ------------------------------------------
echo   STARTING VISION CAPTIONER (Chinese UI)
echo ------------------------------------------
echo.
echo Activating environment...
call "venv\Scripts\activate.bat"

echo.
echo Launching GUI...
echo (The Splash Screen should appear momentarily)

start "" pythonw.exe launcher.py

endlocal
