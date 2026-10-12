@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Medical QA 5.0 - Package for Sharing

echo ============================================================
echo   Package for sharing  (strips keys / data / dev files)
echo ============================================================
echo.

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

"%PYEXE%" -u "tools\package.py" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [ERROR] Packaging failed with code %RC%
pause
