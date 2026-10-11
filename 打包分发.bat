@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Medical QA 5.0 - Package for Sharing

echo ============================================================
echo   打包分发包（去掉密钥、账号、开发文件）
echo ============================================================
echo.

set "PYEXE="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE (
    echo [ERROR] 没有找到 Python 3。
    echo.
    pause
    exit /b 1
)

"%PYEXE%" -u "tools\package.py" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [ERROR] 打包失败，返回码 %RC%
pause
