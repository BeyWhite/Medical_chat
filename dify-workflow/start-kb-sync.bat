@echo off
chcp 65001 >nul
cd /d "%~dp0"
title KB Sync - Auto add new datasets to retrieval node

echo ============================================================
echo   Q&A Workflow - Knowledge Base Sync (watcher)
echo ============================================================
echo.
echo   This keeps the app's "Knowledge Retrieval" node in sync:
echo   whenever a NEW knowledge base is created (by the file branch),
echo   it is appended to the node and the app is re-published.
echo.
echo   Keep this window OPEN. Press Ctrl+C to stop.
echo ============================================================
echo.

set "PYEXE="
if exist "D:\software\bag\Python\Python311\python.exe" set "PYEXE=D:\software\bag\Python\Python311\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PYEXE=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        if not defined PYEXE set "PYEXE=%%i"
    )
)
if not defined PYEXE (
    echo [ERROR] Python 3 not found. Please install Python 3 and add it to PATH.
    pause
    exit /b
)

echo [INFO] Python: %PYEXE%
echo.

"%PYEXE%" -u wf_deploy.py sync-datasets --watch

echo.
echo Sync stopped.
pause
