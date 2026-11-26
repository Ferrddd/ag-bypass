@echo off
call "%~dp0ag.bat" uninstall %*
exit /b %ERRORLEVEL%
