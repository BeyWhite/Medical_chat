@echo off
chcp 65001 >nul
REM Delayed expansion is REQUIRED for the !PID! references inside if-blocks.
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Medical QA 5.0 Server - Restart

echo ============================================================
echo   Medical QA 5.0  -  Restart Server
echo ============================================================
echo.

REM ---- 1. Stop first (same logic as stop.bat) ----
set "STOPPED="
set "OLDPID="
if exist "data\server.pid" set /p OLDPID=<"data\server.pid"
if defined OLDPID (
    echo [1/2] Stopping old server PID=!OLDPID! ...
    taskkill /PID !OLDPID! /F >nul 2>nul
    if not errorlevel 1 set "STOPPED=1"
)
if not defined STOPPED (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8001" ^| findstr "LISTENING" 2^>nul') do (
        echo [1/2] Stopping process on port 8001, PID=%%p ...
        taskkill /PID %%p /F >nul 2>nul
        set "STOPPED=1"
    )
)
if not defined STOPPED echo [1/2] No running server found.
if exist "data\server.pid" del /q "data\server.pid" >nul 2>nul

REM Brief pause so the port is fully released before rebinding.
ping -n 3 127.0.0.1 >nul

REM ---- 2. Start again (same lookup as start.bat) ----
set "PYEXE="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if not defined PYEXE if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
)
if not defined PYEXE (
    echo [ERROR] Python 3 was not found. Install Python 3.8+ and add it to PATH.
    echo.
    pause
    exit /b 1
)

echo [2/2] Starting ...
echo.
echo [INFO] Python : %PYEXE%
echo [INFO] The access URL will be printed below.
echo [INFO] Default URL: http://127.0.0.1:8001
echo [INFO] Keep this window open while using. Press Ctrl+C to stop.
echo ============================================================
echo.

"%PYEXE%" -u server.py
echo.
echo Server stopped.
pause
