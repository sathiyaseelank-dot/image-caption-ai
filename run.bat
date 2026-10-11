@echo off
REM ============================================================
REM  Image Caption Generator AI - Launcher
REM ============================================================
REM  Starts the app and opens your browser.
REM ============================================================

setlocal
title Image Caption Generator AI
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo.
    echo   The app is not installed yet.
    echo   Please run install.bat first.
    echo.
    pause
    exit /b 1
)

echo.
echo   Starting Image Caption Generator AI...
echo   (First start takes ~30 seconds while the AI model loads)
echo.
echo   Your browser will open at http://127.0.0.1:7860
echo.
echo   KEEP THIS WINDOW OPEN while using the app.
echo   Close it (or press Ctrl+C) to stop the app.
echo.

REM Open the browser a few seconds after the server starts
start "" /b cmd /c "timeout /t 12 /nobreak >nul & start http://127.0.0.1:7860"

REM Run the app (blocking - keeps this window open)
"venv\Scripts\python.exe" app.py

echo.
echo   The app has stopped.
pause
