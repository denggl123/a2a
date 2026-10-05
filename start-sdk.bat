@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m a2n_node.product_cli start --background --open %*
if errorlevel 1 (
  pause
  exit /b 1
)
