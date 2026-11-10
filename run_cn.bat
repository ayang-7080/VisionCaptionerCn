@echo off
REM Use --check to validate dependencies, or --selftest for Chinese UI hooks.
call "%~dp0launch_windows.bat" launcher.py auto %*
exit /b %errorlevel%
