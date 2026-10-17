@echo off
setlocal EnableExtensions
rem Always keep a command window open when this batch is double clicked.
if /I "%~1"=="--inside" goto :build
start "MaxCompress Build - diagnostic console" "%ComSpec%" /k ""%~f0" --inside"
if errorlevel 1 (
  echo Failed to start command prompt.
  pause
)
exit /b

:build
chcp 65001 >nul
cd /d "%~dp0"
title MaxCompress Windows EXE Builder
set "LOG=%~dp0build_error_log.txt"
echo MaxCompress build started at %DATE% %TIME% > "%LOG%"
echo Directory: %CD% >> "%LOG%"
echo.
echo ================================================
echo MaxCompress - Windows EXE Build / Error Console
echo The window will stay open after the build.
echo Diagnostic log: %LOG%
echo ================================================
echo.

set "PY=python"
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -V >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
%PY% -V
if errorlevel 1 (
 echo ERROR: Python 3 is missing or not on PATH.
 echo Python 3 not found >> "%LOG%"
 goto :fail
)
%PY% -V >> "%LOG%" 2>&1
%PY% -m pip --version
if errorlevel 1 (
  echo ERROR: pip is unavailable. Trying ensurepip...
  %PY% -m ensurepip --upgrade
  if errorlevel 1 goto :fail
)

set "MIRROR1=https://pypi.tuna.tsinghua.edu.cn/simple"
set "MIRROR2=https://mirrors.aliyun.com/pypi/simple/"
set "MIRROR3=https://pypi.org/simple"
call :install "%MIRROR1%"
if not errorlevel 1 goto :deps_done
call :install "%MIRROR2%"
if not errorlevel 1 goto :deps_done
call :install "%MIRROR3%"
if errorlevel 1 goto :fail

:deps_done
if exist "ffmpeg.exe" (
 echo Checking existing FFmpeg...
)
if 1==1 (
 echo.
 echo [2/3] Downloading FFmpeg for Windows...
 echo FFmpeg download details: ffmpeg_download.log
 echo Downloading FFmpeg >> "%LOG%"
 powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0get_ffmpeg.ps1"
 if errorlevel 1 (
  echo FFmpeg download failed >> "%LOG%"
  goto :fail
 )
)

echo.
echo [3/3] Building standalone Windows executable...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name MaxCompress --add-binary "ffmpeg.exe;." --add-data "assets;assets" --icon "assets\app.ico" app.py
if errorlevel 1 (
 echo PyInstaller failed >> "%LOG%"
 goto :fail
)
if not exist "dist\MaxCompress.exe" (
 echo ERROR: dist\MaxCompress.exe was not created.
 echo Expected EXE does not exist >> "%LOG%"
 goto :fail
)
echo.
echo SUCCESS! EXE: "%CD%\dist\MaxCompress.exe"
echo SUCCESS: dist\MaxCompress.exe >> "%LOG%"
explorer "%CD%\dist"
echo.
echo Window will stay open for diagnostics. You can close it yourself.
exit /b 0

:install
echo.
echo [1/3] Installing dependencies: %~1
echo Trying PyPI mirror %~1 >> "%LOG%"
%PY% -m pip install --disable-pip-version-check --retries 2 --timeout 20 --index-url "%~1" -r requirements.txt pyinstaller
if errorlevel 1 (
 echo Failed on mirror %~1 >> "%LOG%"
 exit /b 1
)
echo Dependencies installed >> "%LOG%"
exit /b 0

:fail
echo.
echo -----------------------------------------------
echo BUILD FAILED. Please copy the red error lines.
echo Log file: "%LOG%"
echo If the error is not in the log, copy the console text.
echo This window will remain open.
echo -----------------------------------------------
echo FAILED at %DATE% %TIME% >> "%LOG%"
exit /b 1
