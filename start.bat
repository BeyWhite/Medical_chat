@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Medical QA 5.0 Server

echo ============================================================
echo   Medical QA 5.0  -  Local Server
echo ============================================================
echo.

REM ---- 1. 找一个可用的 Python 3（不写死任何安装路径）----
set "PYEXE="
REM 1a. Windows 官方启动器 py -3
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
REM 1b. PATH 里的 python
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
REM 1c. 常见的按用户安装目录（%LOCALAPPDATA%\Programs\Python\Python3xx）
if not defined PYEXE for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if not defined PYEXE if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
)
if not defined PYEXE (
    echo [ERROR] 没有找到 Python 3。
    echo         请到 https://www.python.org/downloads/ 安装 Python 3.8 或更高版本，
    echo         安装时勾选 "Add python.exe to PATH"，然后重新双击本脚本。
    echo.
    pause
    exit /b 1
)

echo [INFO] Python : %PYEXE%
echo [INFO] 启动中，稍后会打印访问地址（默认 http://127.0.0.1:8001）
echo [INFO] 使用期间请保持本窗口开着。按 Ctrl+C 停止。
echo ============================================================
echo.

"%PYEXE%" -u server.py
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [INFO] 服务异常退出，返回码 %RC% ^(端口被占用时请先运行 stop.bat^)。
) else (
    echo Server stopped.
)
pause
