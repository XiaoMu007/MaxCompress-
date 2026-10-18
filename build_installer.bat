@echo off
setlocal EnableExtensions
if /I "%~1"=="--inside" goto :main
start "MaxCompress Stable Installer" "%ComSpec%" /k ""%~f0" --inside"
exit /b
:main
chcp 65001 >nul
cd /d "%~dp0"
set "LOG=%CD%\installer_build.log"
echo Starting v2.6.11 installer %DATE% %TIME% > "%LOG%"
echo [1/3] Building and verifying ONEDIR runtime...
call "%~dp0build_windows.bat" --inside
if errorlevel 1 goto :failed
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
 for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1" /v InstallLocation 2^>nul ^| findstr /I "InstallLocation"') do set "ISCC=%%bISCC.exe"
)
if not exist "%ISCC%" (
 echo Inno Setup 6 missing. Install on BUILD PC: https://jrsoftware.org/isdl.php
 goto :failed
)
echo [2/3] Building Inno Setup EXE from entire dist directory...
"%ISCC%" "installer.iss" >> "%LOG%" 2>&1
if errorlevel 1 goto :failed
if not exist "installer_output\MaxCompress_Setup_v2.6.11.exe" goto :failed
echo [3/3] Done. Install and test on Windows before GitHub Release.
explorer "%CD%\installer_output"
exit /b 0
:failed
echo ERROR. See "%LOG%" and build_error_log.txt
if exist "%LOG%" type "%LOG%"
exit /b 1
