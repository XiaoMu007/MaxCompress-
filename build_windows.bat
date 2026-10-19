@echo off
setlocal EnableExtensions
if /I "%~1"=="--inside" goto :main
start "MaxCompress Stable EXE Build" "%ComSpec%" /k ""%~f0" --inside"
exit /b
:main
chcp 65001 >nul
cd /d "%~dp0"
set "LOG=%CD%\build_error_log.txt"
echo MaxCompress v2.6.15 ONEDIR build %DATE% %TIME% > "%LOG%"
set "PY=python"
where py >nul 2>&1
if not errorlevel 1 (py -3 -V >nul 2>&1 && set "PY=py -3")
%PY% -V || goto :failed
echo [1/5] Preparing isolated build environment
if not exist ".build-venv\Scripts\python.exe" (
 %PY% -m venv ".build-venv" || goto :failed
)
set "VPY=%CD%\.build-venv\Scripts\python.exe"
echo [2/5] Installing packages with mirror fallback
"%VPY%" -m pip install --disable-pip-version-check --timeout 45 -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple >> "%LOG%" 2>&1
if errorlevel 1 (
 "%VPY%" -m pip install --disable-pip-version-check --timeout 45 -r requirements.txt pyinstaller -i https://mirrors.aliyun.com/pypi/simple/ >> "%LOG%" 2>&1
 if errorlevel 1 (
  "%VPY%" -m pip install --disable-pip-version-check --timeout 45 -r requirements.txt pyinstaller -i https://pypi.org/simple >> "%LOG%" 2>&1
  if errorlevel 1 goto :failed
 )
)
echo [3/5] Checking FFmpeg
if exist "ffmpeg.exe" (
 "ffmpeg.exe" -hide_banner -version >nul 2>&1
 if errorlevel 1 del /q "ffmpeg.exe"
)
if not exist "ffmpeg.exe" (
 powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%CD%\get_ffmpeg.ps1" >> "%LOG%" 2>&1
 if errorlevel 1 goto :failed
)
echo [4/5] Building ONEDIR application (NOT onefile)
"%VPY%" -m PyInstaller --noconfirm --clean --onedir --windowed --name MaxCompress --add-binary "ffmpeg.exe;." --add-data "assets;assets" --icon "assets\app.ico" app.py >> "%LOG%" 2>&1
if errorlevel 1 goto :failed
echo [5/5] Verifying bundled runtime
"%VPY%" verify_bundle.py "dist\MaxCompress" >> "%LOG%" 2>&1
if errorlevel 1 goto :failed
echo.
echo SUCCESS: dist\MaxCompress\MaxCompress.exe
echo IMPORTANT: Keep the entire dist\MaxCompress directory together.
explorer "%CD%\dist\MaxCompress"
exit /b 0
:failed
echo.
echo BUILD FAILED. See: "%LOG%"
type "%LOG%"
exit /b 1
