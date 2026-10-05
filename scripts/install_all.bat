@echo off
REM Compatibility alias for the canonical installer.
call "%~dp0bootstrap.cmd" %*
exit /b %errorlevel%
