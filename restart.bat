@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Medical QA 5.0 Server - Restart

echo ============================================================
echo   Medical QA 5.0  -  Restart Server
echo ============================================================
echo.

REM ---- 1. 先停（与 stop.bat 同一套逻辑）----
set "STOPPED="
if exist "data\server.pid" (
    set /p PID=<"data\server.pid"
    if defined PID (
        echo [1/2] 停止旧服务 PID=!PID! ...
        taskkill /PID !PID! /F >nul 2>nul
        if not errorlevel 1 set "STOPPED=1"
    )
)
if not defined STOPPED (
    for /f "skip=1 tokens=1" %%p in ('wmic process where "name='python.exe' and commandline like '%%server.py%%'" get processid 2^>nul') do (
        if not "%%p"=="" (
            echo [1/2] 停止 python 进程 PID=%%p ...
            taskkill /PID %%p /F >nul 2>nul
            set "STOPPED=1"
        )
    )
)
if not defined STOPPED (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8001" ^| findstr "LISTENING" 2^>nul') do (
        echo [1/2] 停止占用 8001 端口的进程 PID=%%p ...
        taskkill /PID %%p /F >nul 2>nul
        set "STOPPED=1"
    )
)
if not defined STOPPED echo [1/2] 没有正在运行的服务。
if exist "data\server.pid" del /q "data\server.pid" >nul 2>nul

timeout /t 1 /nobreak >nul

REM ---- 2. 再启（与 start.bat 同一套探测）----
set "PYEXE="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if not defined PYEXE if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
)
if not defined PYEXE (
    echo [ERROR] 没有找到 Python 3。请安装 Python 3.8+ 并加入 PATH。
    echo.
    pause
    exit /b 1
)

echo [2/2] 启动中 ...
echo.
echo [INFO] Python : %PYEXE%
echo [INFO] 稍后会打印访问地址（默认 http://127.0.0.1:8001）
echo [INFO] 使用期间请保持本窗口开着。按 Ctrl+C 停止。
echo ============================================================
echo.

"%PYEXE%" -u server.py
echo.
echo Server stopped.
pause
