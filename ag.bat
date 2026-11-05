@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [!] Python 3 not found in PATH. Please install Python 3.
    exit /b 1
)
python "%SCRIPT_DIR%ag" %*
exit /b %ERRORLEVEL%
