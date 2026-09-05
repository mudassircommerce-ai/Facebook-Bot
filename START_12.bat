@echo off
title FB Group Joiner - Profile 12
cd /d "%~dp0"

py --version >nul 2>&1
if errorlevel 1 goto NOPY

py -c "import playwright, pgeocode, numpy, geopy" >nul 2>&1
if not errorlevel 1 goto RUN

echo ============================================================
echo  First-time setup - installing components (2-5 minutes)...
echo  Needs an internet connection. Only happens once.
echo ============================================================
py -m pip install --disable-pip-version-check playwright pgeocode numpy geopy
if errorlevel 1 goto FAIL
py -m playwright install chromium
if errorlevel 1 goto FAIL

:RUN
py fb_joiner.py 12
if errorlevel 1 pause
exit /b 0

:NOPY
echo.
echo  Python is not installed.
echo  1) Download from https://www.python.org/downloads/
echo  2) On the FIRST installer screen, TICK "Add python.exe to PATH"
echo  3) Install, then double-click START.bat again.
echo.
pause
exit /b 1

:FAIL
echo.
echo  Setup failed. Check the internet connection and run START.bat again.
echo.
pause
exit /b 1
