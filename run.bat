@echo off
REM ============================================================
REM  Image Caption Generator AI - Launcher
REM ============================================================
REM  Starts the app and opens your browser.
REM  If the app is not set up yet, it sets itself up first.
REM ============================================================

setlocal EnableDelayedExpansion
title Image Caption Generator AI
cd /d "%~dp0"

REM ------------------------------------------------------------
REM  First run? Set everything up automatically.
REM ------------------------------------------------------------
if not exist "venv\Scripts\python.exe" (
    echo.
    echo  ============================================================
    echo    FIRST RUN - SETTING UP
    echo  ============================================================
    echo.
    echo   This is a one-time setup. It will take 10-20 minutes.
    echo   Please do not close this window.
    echo.
    pause

    REM --- Find Python ---
    set "PYTHON_CMD="
    py -3.12 --version >nul 2>&1
    if !errorlevel! equ 0 set "PYTHON_CMD=py -3.12"

    if not defined PYTHON_CMD (
        python --version >nul 2>&1
        if !errorlevel! equ 0 set "PYTHON_CMD=python"
    )

    if not defined PYTHON_CMD (
        echo.
        echo   Python was not found. Trying to install it...
        winget --version >nul 2>&1
        if !errorlevel! equ 0 (
            winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
            set "PATH=!PATH!;%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%ProgramFiles%\Python312;%ProgramFiles%\Python312\Scripts"
            py -3.12 --version >nul 2>&1
            if !errorlevel! equ 0 set "PYTHON_CMD=py -3.12"
        )
    )

    if not defined PYTHON_CMD (
        echo.
        echo  ============================================================
        echo   ERROR: Python is required but was not found.
        echo  ============================================================
        echo.
        echo   Please install Python 3.12 manually:
        echo     1. Go to: https://www.python.org/downloads/
        echo     2. Download Python 3.12 (Windows installer 64-bit)
        echo     3. IMPORTANT: tick "Add python.exe to PATH"
        echo     4. Run this launcher again.
        echo.
        pause
        exit /b 1
    )

    echo   Using: !PYTHON_CMD!
    echo.

    REM --- Create venv ---
    echo   Creating environment...
    !PYTHON_CMD! -m venv venv
    if !errorlevel! neq 0 (
        echo.
        echo   ERROR: Could not create the environment.
        pause
        exit /b 1
    )

    REM --- Install packages ---
    echo   Installing PyTorch (CPU, ~200 MB)...
    "venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    "venv\Scripts\python.exe" -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    if !errorlevel! neq 0 (
        echo.
        echo   ERROR: PyTorch install failed. Check your internet connection.
        pause
        exit /b 1
    )

    echo   Installing Transformers, Pillow and Gradio...
    "venv\Scripts\python.exe" -m pip install transformers Pillow gradio
    if !errorlevel! neq 0 (
        echo.
        echo   ERROR: Package install failed. Check your internet connection.
        pause
        exit /b 1
    )

    if not exist "model_cache" mkdir "model_cache"

    echo.
    echo   Setup complete.
    echo.
)

REM ------------------------------------------------------------
REM  Start the app
REM ------------------------------------------------------------
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
