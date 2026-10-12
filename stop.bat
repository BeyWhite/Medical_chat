@echo off
chcp 65001 >nul
REM Delayed expansion is REQUIRED: we read the PID inside an if-block,
REM so the value must be referenced as !PID! (not %PID%).
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Medical QA 5.0 Server - Stop

echo ============================================================
echo   Stop Medical QA 5.0 Server
echo ============================================================
echo.

set "STOPPED="

REM ---- Method 1 (preferred): PID file written by server.py. Port independent. ----
set "OLDPID="
if exist "data\server.pid" set /p OLDPID=<"data\server.pid"
if defined OLDPID (
    echo [INFO] Stopping PID=!OLDPID! from data\server.pid ...
    taskkill /PID !OLDPID! /F >nul 2>nul
    if not errorlevel 1 set "STOPPED=1"
) else (
    echo [INFO] No data\server.pid found.
)

REM ---- Method 2 (fallback): whatever listens on the default port 8001 ----
if not defined STOPPED (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8001" ^| findstr "LISTENING" 2^>nul') do (
        echo [INFO] Stopping process on port 8001, PID=%%p ...
        taskkill /PID %%p /F >nul 2>nul
        set "STOPPED=1"
    )
)

if exist "data\server.pid" del /q "data\server.pid" >nul 2>nul

REM Give the OS a brief moment to release the port before reporting.
if defined STOPPED ping -n 2 127.0.0.1 >nul

if not defined STOPPED (
    echo [INFO] No running server found. It may already be stopped.
) else (
    echo.
    echo [OK] Stopped.
)
echo.
pause
