@echo off
setlocal
set "AG_UAC=1"
call "%~dp0ag.bat" uninstall %*
exit /b %ERRORLEVEL%
