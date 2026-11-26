@echo off
call "%~dp0ag.bat" install %*
exit /b %ERRORLEVEL%
