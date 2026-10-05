@echo off
REM Full desktop SDK installer. Keep this wrapper ASCII-only.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_desktop.ps1" %*
if errorlevel 1 (
  echo Installation failed. See the message above.
  pause
  exit /b 1
)
