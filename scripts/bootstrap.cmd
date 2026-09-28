@echo off
REM One-click install (Windows). Double-click this file.
REM All real work lives in scripts\bootstrap.py; this only finds Python and hands off.
REM NOTE: keep this file ASCII-only (cmd.exe parses it as the system code page).
setlocal
cd /d "%~dp0.."
set "PY=python"
where python >nul 2>nul || set "PY=py"
%PY% scripts\bootstrap.py %*
if errorlevel 1 (
  echo.
  echo Installation failed. See the message above.
  pause
  exit /b 1
)
echo.
echo Done. Next step is printed above.
pause
