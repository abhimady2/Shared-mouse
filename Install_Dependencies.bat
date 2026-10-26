@echo off
title Install Dependencies - ShareLink DuoCast
cd /d "%~dp0"
echo ========================================================
echo   Installing ShareLink DuoCast Dependencies...
echo ========================================================
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo ========================================================
echo   Installation Complete! Double-click Run_ShareLink.bat to start.
echo ========================================================
pause
