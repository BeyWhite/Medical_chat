@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Medical QA 5.0 Server - Stop

echo ============================================================
echo   Stop Medical QA 5.0 Server
echo ============================================================
echo.

set "STOPPED="

REM ---- 方式 1（首选）：读 server.py 写下的 PID 文件，与端口无关 ----
if exist "data\server.pid" (
    set /p PID=<"data\server.pid"
    if defined PID (
        echo [INFO] 按 PID 文件停止进程 PID=!PID! ...
        taskkill /PID !PID! /F >nul 2>nul
        if not errorlevel 1 set "STOPPED=1"
    )
)

REM ---- 方式 2（兜底）：按命令行里含 server.py 的 python 进程查找 ----
if not defined STOPPED (
    for /f "skip=1 tokens=1" %%p in ('wmic process where "name='python.exe' and commandline like '%%server.py%%'" get processid 2^>nul') do (
        if not "%%p"=="" (
            echo [INFO] 停止 python 进程 PID=%%p ...
            taskkill /PID %%p /F >nul 2>nul
            set "STOPPED=1"
        )
    )
)

REM ---- 方式 3（最后兜底）：默认端口 8001 ----
if not defined STOPPED (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8001" ^| findstr "LISTENING" 2^>nul') do (
        echo [INFO] 停止占用 8001 端口的进程 PID=%%p ...
        taskkill /PID %%p /F >nul 2>nul
        set "STOPPED=1"
    )
)

if exist "data\server.pid" del /q "data\server.pid" >nul 2>nul

if not defined STOPPED (
    echo [INFO] 没有找到正在运行的服务，可能已经停止了。
) else (
    echo.
    echo [OK] 已停止。
)
echo.
pause
