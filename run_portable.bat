@echo off
setlocal

REM Portable launcher: uses the embedded runtime\ Python, no installation needed.
REM Copy the whole project folder (venv + models included) to any Win10/11 x64 machine.
REM Chinese UI via launcher.py; change launcher.py to main.py for the English UI.

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

title Launching VisionCaptioner (Portable)...

echo.
echo ------------------------------------------
echo   STARTING VISION CAPTIONER (Portable)
echo ------------------------------------------
echo.

REM Bypass the venv redirector: embedded Python + PYTHONPATH to venv site-packages
set "PYTHONPATH=%SCRIPT_DIR%venv\Lib\site-packages"
REM Defense: a system-wide PYTHONHOME would break stdlib resolution of the embedded interpreter
set "PYTHONHOME="

start "" "runtime\pythonw.exe" launcher.py

endlocal
