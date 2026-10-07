@echo off
REM Keep this scheduled-task entry ASCII-only. All launchers use the desktop SDK.
setlocal
cd /d "%~dp0.."
set "A2N_DESKTOP_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%A2N_DESKTOP_PY%" (
  echo [desktop-node] Run install.bat first. >&2
  exit /b 1
)
if /i "%~1"=="loop" goto supervise
"%A2N_DESKTOP_PY%" -u scripts\serve_desktop_node.py --watch-existing
exit /b %ERRORLEVEL%
:supervise
"%A2N_DESKTOP_PY%" -u scripts\serve_desktop_node.py --watch-existing
REM Reuse and an explicitly stopped node are normal exits, not crashes.
if not errorlevel 1 exit /b 0
 echo [supervisor] Desktop SDK exited, restarting in 5 seconds. >&2
"%A2N_DESKTOP_PY%" -c "import time; time.sleep(5)"
goto supervise
