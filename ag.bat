@echo off
setlocal
if defined AG_UAC (
    net session >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        if defined _AG_ELEVATED (
            echo [!] Administrator privileges are required.
            pause
            exit /b 1
        )
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34; $bat='%~f0'; $argsStr='%*'; $p=Start-Process cmd.exe -ArgumentList ('/c ' + $q + 'set _AG_ELEVATED=1 && call ' + $q + $bat + $q + ' ' + $argsStr + $q) -Verb RunAs -PassThru -Wait; exit $p.ExitCode"
        exit /b %ERRORLEVEL%
    )
)
set "SCRIPT_DIR=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "RC=1"
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python "%SCRIPT_DIR%ag" %*
    set "RC=%ERRORLEVEL%"
    goto :done
)
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    py -3 "%SCRIPT_DIR%ag" %*
    set "RC=%ERRORLEVEL%"
    goto :done
)
echo [!] Python 3 not found in PATH. Please install Python 3.
:done
if defined _AG_ELEVATED pause
exit /b %RC%
