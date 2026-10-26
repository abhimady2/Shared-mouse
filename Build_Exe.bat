@echo off
title Build Portable EXE - ShareLink DuoCast
cd /d "%~dp0"
echo ========================================================
echo   Building ShareLink_DuoCast.exe (portable, single file)
echo ========================================================
python -m pip show pyinstaller >nul 2>&1 || python -m pip install pyinstaller
python -m PyInstaller ShareLink.spec --noconfirm
if %errorlevel% neq 0 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)
echo.
echo ========================================================
echo   Done: dist\ShareLink_DuoCast.exe
echo   That single file is portable - copy it anywhere and
echo   run it. config.json is created next to it on first run.
echo ========================================================
pause
