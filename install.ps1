# Antigravity Windows Installer
[CmdletBinding()]
param()

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1. Check Administrator Privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] Ошибка: Для установки требуются права Администратора." -ForegroundColor Red
    Write-Host "    Запустите PowerShell от имени Администратора (Run as Administrator)." -ForegroundColor Yellow
    exit 1
}

# 2. Verify Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[!] Ошибка: Python 3 не найден в PATH." -ForegroundColor Red
    Write-Host "    Установите Python 3 и добавьте его в системный PATH." -ForegroundColor Yellow
    exit 1
}

# 3. Read interval_minutes from config.json
$ConfigPath = Join-Path $ScriptDir "config.json"
$Interval = 2
if (Test-Path $ConfigPath) {
    try {
        $cfg = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
        if ($cfg.healthcheck -and $cfg.healthcheck.interval_minutes -gt 0) {
            $Interval = [int]$cfg.healthcheck.interval_minutes
        }
    } catch {
        Write-Host "[WARN] Не удалось прочитать interval_minutes из config.json, используется значение по умолчанию ($Interval мин)." -ForegroundColor Yellow
    }
}

# 4. Configure Task Scheduler (schtasks)
Write-Host "[*] Настройка задачи в Планировщике задач Windows (интервал: $Interval мин)..." -ForegroundColor Cyan
$TaskRun = "python `"$ScriptDir\ag`" check --quiet"
& schtasks /create /tn "AntigravityBypassCheck" /tr $TaskRun /sc minute /mo $Interval /f
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Ошибка создания задачи в schtasks." -ForegroundColor Red
    exit 1
}
Write-Host "[✓] Задача 'AntigravityBypassCheck' создана в Планировщике задач." -ForegroundColor Green

# 5. Add ScriptDir to User Path
Write-Host "[*] Добавление $ScriptDir в переменную среды Path..." -ForegroundColor Cyan
$userPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
if (-not $userPath) {
    $userPath = ""
}
$pathParts = $userPath -split ";" | Where-Object { $_ -ne "" }
if ($pathParts -notcontains $ScriptDir) {
    $newPath = ($pathParts + $ScriptDir) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, [EnvironmentVariableTarget]::User)
    Write-Host "[✓] Каталог $ScriptDir добавлен в User Path." -ForegroundColor Green
} else {
    Write-Host "[i] Каталог $ScriptDir уже присутствует в User Path." -ForegroundColor Gray
}

# 6. Instructions
Write-Host ""
Write-Host "=========================================================" -ForegroundColor Green
Write-Host " Antigravity Bypass для Windows успешно установлен!" -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
Write-Host "Использование в командной строке или PowerShell:"
Write-Host "  ag on       - активировать обход и настроить маршрутизацию"
Write-Host "  ag status   - проверить статус узла, эндпоинта и планировщика"
Write-Host "  ag nodes    - проверить задержку всех SNI-узлов"
Write-Host "  ag check    - ручная проверка и автоисправление отвала узла"
Write-Host "  ag off      - выключить обход и удалить записи"
Write-Host "  ag unpatch  - восстановить оригинальный файл бинарника"
Write-Host ""
Write-Host "Фоновый мониторинг активен (запуск каждые $Interval мин)." -ForegroundColor Cyan
