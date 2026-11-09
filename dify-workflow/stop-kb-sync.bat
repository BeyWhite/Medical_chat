@echo off
chcp 65001 >nul
title Stop KB Sync

echo Stopping "KB Sync" watcher ...
taskkill /FI "WINDOWTITLE eq KB Sync - Auto add new datasets to retrieval node*" /F >nul 2>nul
if %errorlevel%==0 (
    echo [OK] KB Sync watcher stopped.
) else (
    echo [INFO] No running "KB Sync" window found.
    echo        If it is running in a console you opened manually, press Ctrl+C there.
)
echo.
pause
