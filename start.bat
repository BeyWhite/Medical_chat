@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Medical QA 5.0 Server

echo ============================================================
echo   Medical QA 5.0  -  Local Server
echo ============================================================
echo.

REM ---- 1. Find a usable Python 3 (no hard-coded install path) ----
set "PYEXE="
REM 1a. Windows Python launcher: py -3
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
REM 1b. python on PATH
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
REM 1c. common per-user install dir
if not defined PYEXE for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if not defined PYEXE if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
)
if not defined PYEXE (
    echo [ERROR] Python 3 was not found.
    echo         Install Python 3.8+ from https://www.python.org/downloads/
    echo         During setup, tick "Add python.exe to PATH".
    echo         Then double-click this script again.
    echo.
    pause
    exit /b 1
)

echo [INFO] Python : %PYEXE%
echo [INFO] Starting... the access URL will be printed below.
echo [INFO] Default URL: http://127.0.0.1:8001
echo [INFO] Keep this window open while using. Press Ctrl+C to stop.
echo ============================================================
echo.

"%PYEXE%" -u server.py
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [INFO] Server exited with code %RC%.
    echo        If the port was already in use, run stop.bat first.
) else (
    echo Server stopped.
)
pause
