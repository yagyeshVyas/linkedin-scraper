@echo off
setlocal
cd /d "%~dp0"
title Rack - LinkedIn control room

rem ── one double-click boot: venv, deps, browsers, dashboard ──
if not exist ".venv\Scripts\python.exe" (
    echo [setup] first run: creating .venv ...
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if not exist ".venv\Scripts\python.exe" (
        echo [setup] could not create .venv - install Python 3.10+ first.
        pause
        exit /b 1
    )
    echo [setup] installing python packages ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r Requirements.txt
    echo [setup] installing chromium builds ...
    ".venv\Scripts\python.exe" -m playwright install chromium
    ".venv\Scripts\python.exe" -m patchright install chromium
)

echo [rack] starting dashboard ...
".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo [rack] dashboard exited with an error.
    pause
)
