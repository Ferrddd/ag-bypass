#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Установка Antigravity Windows Bypass
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "        Установка Antigravity Windows Bypass" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Проверка прав администратора
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] Ошибка: Скрипт установки необходимо запустить от имени Администратора!" -ForegroundColor Red
    Write-Host "    Нажмите правой кнопкой мыши по файлу install.bat и выберите Запуск от имени администратора." -ForegroundColor Yellow
    Exit 1
}

# 2. Проверка наличия Python
$pyCmd = $null
if (Get-Command "py" -ErrorAction SilentlyContinue) {
    $pyCmd = "py"
} elseif (Get-Command "python" -ErrorAction SilentlyContinue) {
    $pyCmd = "python"
}

if (-not $pyCmd) {
    Write-Host "[!] Python 3 не обнаружен в системе!" -ForegroundColor Red
    Write-Host "    Пожалуйста, установите Python 3 с официального сайта https://www.python.org/" -ForegroundColor Yellow
    Write-Host "    Обязательно поставьте галочку Add Python to PATH при установке." -ForegroundColor Yellow
    Exit 1
}

$pyVer = & $pyCmd --version 2>&1
Write-Host "[+] Найден Python: $pyVer" -ForegroundColor Green

# 3. Добавление текущей папки в PATH пользователя
$currentPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
if ($currentPath -notlike "*$ScriptDir*") {
    $newPath = "$currentPath;$ScriptDir".Trim(";")
    [Environment]::SetEnvironmentVariable("Path", $newPath, [EnvironmentVariableTarget]::User)
    $env:Path = "$env:Path;$ScriptDir"
    Write-Host "[+] Папка программы добавлена в PATH ($ScriptDir)" -ForegroundColor Green
} else {
    Write-Host "[+] Папка программы уже находится в PATH." -ForegroundColor Gray
}

# 4. Регистрация задания в Планировщике Windows (Task Scheduler)
Write-Host "[*] Настройка задачи фонового мониторинга в Планировщике Windows..." -ForegroundColor Cyan

$pyExe = (Get-Command "python" -ErrorAction SilentlyContinue).Source
if (-not $pyExe) {
    $pyExe = (Get-Command "py" -ErrorAction SilentlyContinue).Source
}
$pyDir = Split-Path -Parent $pyExe
$pythonw = Join-Path $pyDir "pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $pythonw = $pyExe
}

$taskName = "AntigravityBypass"
$agScript = Join-Path $ScriptDir "ag.py"

# Регистрация через schtasks для максимальной надежности
$tr = "`"$pythonw`" `"$agScript`" check --quiet"
$schCmd = "schtasks /create /tn `"$taskName`" /tr `"$tr`" /sc minute /mo 15 /ru `"SYSTEM`" /f /rl HIGHEST"
$schOut = cmd /c $schCmd 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[+] Задача планировщика $taskName успешно зарегистрирована (каждые 15 минут)." -ForegroundColor Green
} else {
    $schCmdUser = "schtasks /create /tn `"$taskName`" /tr `"$tr`" /sc minute /mo 15 /f /rl HIGHEST"
    $schOutUser = cmd /c $schCmdUser 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[+] Задача планировщика $taskName зарегистрирована для пользователя $($env:USERNAME)." -ForegroundColor Green
    } else {
        Write-Host "[WARN] Не удалось настроить планировщик: $schOutUser" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Установка успешно завершена!" -ForegroundColor Green
Write-Host " Команды управления (выполнять в терминале Администратора):" -ForegroundColor White
Write-Host "   ag on       - Включить обход и пропатчить клиент" -ForegroundColor Yellow
Write-Host "   ag status   - Проверить текущий статус и задержку узла" -ForegroundColor Yellow
Write-Host "   ag nodes    - Протестировать все доступные узлы" -ForegroundColor Yellow
Write-Host "   ag check    - Ручная проверка узла и авто-переключение" -ForegroundColor Yellow
Write-Host "   ag off      - Отключить обход и очистить hosts" -ForegroundColor Yellow
Write-Host "   ag unpatch  - Восстановить оригинальный agy.exe" -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Cyan

Write-Host ""
Read-Host "Нажмите Enter для завершения..."
