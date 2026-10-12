@echo off
title FB Joiner - FIX
cd /d "%~dp0"
echo ============================================================
echo   FIX  -  clearing stuck Chrome and profile locks
echo   (use this if the bot's browser won't open or it keeps
echo    crashing / "already in use")
echo ============================================================
echo.
py fix.py
if errorlevel 1 (
  echo.
  echo  Python not found - make sure Python is installed.
)
echo.
pause
