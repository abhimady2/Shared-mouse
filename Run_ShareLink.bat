@echo off
title ShareLink DuoCast
cd /d "%~dp0"
echo ========================================================
echo   Launching ShareLink DuoCast Mesh Engine...
echo ========================================================
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not found in PATH!
    echo Please install Python 3.10+ from python.org and ensure "Add to PATH" is checked.
    pause
    exit /b 1
)

python main.py %*
if %errorlevel% neq 0 (
    echo.
    echo ShareLink exited with code %errorlevel%.
    pause
)
