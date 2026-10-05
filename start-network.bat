@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_network.ps1 -Open %*
if errorlevel 1 (
  pause
  exit /b 1
)
