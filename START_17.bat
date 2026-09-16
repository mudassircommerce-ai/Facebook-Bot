@echo off
title FB Group Joiner - Profile 17
cd /d "%~dp0"

py --version >nul 2>&1
if errorlevel 1 goto NOPY

py -c "import playwright, pgeocode, numpy, geopy" >nul 2>&1
if errorlevel 1 goto SETUP

rem Packages to hain - ab CHROMIUM browser bhi check karo. Pehle sirf
rem imports dekhte the: agar pip install chal gaya lekin chromium ka
rem download beech mein fail ho gaya, to setup dobara kabhi nahi chalta
rem tha aur "Login to Facebook" par launching error aata tha.
py -c "import os,sys;from playwright.sync_api import sync_playwright;p=sync_playwright().start();sys.exit(0 if os.path.exists(p.chromium.executable_path) else 1)" >nul 2>&1
if not errorlevel 1 goto RUN

echo ============================================================
echo  Browser (Chromium) missing - downloading it now...
echo  Needs an internet connection, takes 1-3 minutes.
echo ============================================================
py -m playwright install chromium
if errorlevel 1 goto FAIL
goto RUN

:SETUP
echo ============================================================
echo  First-time setup - installing components (2-5 minutes)...
echo  Needs an internet connection. Only happens once.
echo ============================================================
py -m pip install --disable-pip-version-check playwright==1.62.0 pgeocode numpy geopy
if errorlevel 1 goto FAIL
py -m playwright install chromium
if errorlevel 1 goto FAIL

:RUN
py fb_joiner.py 17
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
