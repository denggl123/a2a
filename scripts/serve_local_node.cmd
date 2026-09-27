@echo off
REM Windows entry for the local A2N node (Task Scheduler / any supervisor).
REM
REM KEEP THIS FILE ASCII-ONLY. cmd.exe parses .cmd files with the OEM
REM codepage (GBK on a Chinese Windows); UTF-8 Chinese comments get
REM mis-decoded and their fragments are executed as commands (real bug
REM seen 2026-09-27: task ran, exited 0, node never came up).
REM
REM Bash must be located at runtime: Git for Windows may live on any
REM drive (this machine has it on D:, 2026-09-27).
REM
REM Usage:
REM   serve_local_node.cmd        one foreground run (same path as
REM                               manual "bash scripts/serve_local_node.sh")
REM   serve_local_node.cmd loop   supervise mode: restart the node 5s
REM                               after it exits -- this is what the
REM                               scheduled task should call.
setlocal
set "MODE=%~1"
set "SCRIPT=serve_local_node.sh"
if /i "%MODE%"=="loop" set "SCRIPT=supervise_local_node.sh"
set "BASH_EXE=%ProgramFiles%\Git\bin\bash.exe"
if not exist "%BASH_EXE%" set "BASH_EXE=D:\Program Files\Git\bin\bash.exe"
if not exist "%BASH_EXE%" set "BASH_EXE=C:\Program Files (x86)\Git\bin\bash.exe"
if not exist "%BASH_EXE%" (
  echo [local-node] bash.exe not found under Program Files Git locations. >&2
  exit /b 1
)
set "HERE=%~dp0"
set "HERE=%HERE:\=/%"
"%BASH_EXE%" -lc "exec bash '%HERE%%SCRIPT%'"
endlocal
