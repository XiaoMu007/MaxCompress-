@echo off
setlocal EnableExtensions
if /I "%~1"=="--inside" goto :inside
start "MaxCompress Installer Builder" "%ComSpec%" /k ""%~f0" --inside"
exit /b
:inside
chcp 65001 >nul
cd /d "%~dp0"
title MaxCompress - Installer Build
set "LOG=%~dp0installer_build.log"
echo Started: %DATE% %TIME% > "%LOG%"
echo =========================================
echo  MaxCompress Installer Build
 echo =========================================
echo.
set "PY=python"
where py >nul 2>&1
if not errorlevel 1 (py -3 -V >nul 2>&1 && set "PY=py -3")
%PY% -V || goto :no_python
if not exist "ffmpeg.exe" (
 echo [1/4] Download or reuse FFmpeg...
 powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get_ffmpeg.ps1"
 if errorlevel 1 goto :failed
) else (
 echo [1/4] Existing FFmpeg found. Verifying...
 "ffmpeg.exe" -hide_banner -version >nul 2>&1
 if errorlevel 1 (
  del /q "ffmpeg.exe" >nul 2>&1
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get_ffmpeg.ps1"
  if errorlevel 1 goto :failed
 )
)
echo [2/4] Installing build dependencies with mirror fallback...
%PY% -m pip install --disable-pip-version-check --timeout 30 -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
 %PY% -m pip install --disable-pip-version-check --timeout 30 -r requirements.txt pyinstaller -i https://mirrors.aliyun.com/pypi/simple/
 if errorlevel 1 (
  %PY% -m pip install --disable-pip-version-check --timeout 30 -r requirements.txt pyinstaller -i https://pypi.org/simple
  if errorlevel 1 goto :failed
 )
)
echo [3/4] Building distributable application...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name MaxCompress --add-binary "ffmpeg.exe;." --add-data "assets;assets" --icon "assets\app.ico" app.py
if errorlevel 1 goto :failed
if not exist "dist\MaxCompress.exe" goto :failed
echo [4/4] Compiling Inno Setup installer...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
 for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1" /v InstallLocation 2^>nul ^| findstr /I "InstallLocation"') do set "ISCC=%%bISCC.exe"
)
if not exist "%ISCC%" (
 echo.
 echo Inno Setup 6 was not found.
 echo Install once on THIS BUILD PC, not on customer PCs:
 echo https://jrsoftware.org/isdl.php
 echo Then rerun build_installer.bat. You can also open installer.iss in Inno Setup and Compile.
 echo NOTE: dist\MaxCompress.exe is already built.
 goto :failed
)
"%ISCC%" "installer.iss" >> "%LOG%" 2>&1
if errorlevel 1 (
 type "%LOG%"
 goto :failed
)
echo.
echo SUCCESS! Setup EXE is in installer_output
start "" explorer "%~dp0installer_output"
exit /b 0
:no_python
echo ERROR: Python is required only on the packaging PC.
goto :failed
:failed
echo.
echo BUILD FAILED. Check the messages above and installer_build.log.
echo This console stays open.
exit /b 1
