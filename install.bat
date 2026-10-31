@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: Проверка прав администратора через fltmc (работает надежно на любой Windows)
fltmc >nul 2>&1
if %errorLevel% equ 0 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
    goto end
)

echo [*] Запуск установщика с правами Администратора...
powershell -NoProfile -Command "Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"\"%~dp0install.ps1\"\"' -Verb RunAs"
exit /b

:end
pause
