@echo off
setlocal
REM Shared by the English, Chinese and portable launchers.
cd /d "%~dp0"
set "VC_ENTRY=%~1"
set "VC_MODE=%~2"
set "VC_OPTION=%~3"
set "PYTHONHOME="
set "PYTHONPATH="

if /i "%VC_MODE%"=="portable" goto portable
if not exist "venv\Scripts\python.exe" goto portable
"venv\Scripts\python.exe" -c "import encodings" >nul 2>&1
if errorlevel 1 goto portable
set "VC_PYTHON=%~dp0venv\Scripts\python.exe"
set "VC_PYTHONW=%~dp0venv\Scripts\pythonw.exe"
goto ready

:portable
if not exist "runtime\python.exe" goto missing
if not exist "venv\Lib\site-packages" goto missing
set "VC_PYTHON=%~dp0runtime\python.exe"
set "VC_PYTHONW=%~dp0runtime\pythonw.exe"
set "PYTHONPATH=%~dp0venv\Lib\site-packages"
"%VC_PYTHON%" -c "import encodings" >nul 2>&1
if errorlevel 1 goto missing

:ready
echo Using Python: "%VC_PYTHON%"
if /i "%VC_OPTION%"=="--check" goto check
if /i "%VC_OPTION%"=="--selftest" goto selftest
if not "%VC_OPTION%"=="" (
    echo Unknown option. Use --check or --selftest.
    exit /b 2
)
if not exist "%VC_ENTRY%" goto missing
if not exist "%VC_PYTHONW%" goto missing
"%VC_PYTHON%" -c "import PySide6.QtWidgets"
if errorlevel 1 goto failed
start "" "%VC_PYTHONW%" "%VC_ENTRY%"
if errorlevel 1 goto failed
exit /b 0

:check
"%VC_PYTHON%" -c "import PySide6.QtWidgets; import main, backend, tab_captions, tab_review, tab_mask, tab_video, tab_qa; print('Environment check passed.')"
exit /b %errorlevel%

:selftest
"%VC_PYTHON%" launcher.py --selftest
exit /b %errorlevel%

:missing
echo [ERROR] No usable project Python environment was found.
echo Install the venv as described in README.md, or use a complete portable package.
goto failure_exit

:failed
echo [ERROR] Startup failed. Run this launcher with --check for details.

:failure_exit
if "%VC_OPTION%"=="" pause
exit /b 1
