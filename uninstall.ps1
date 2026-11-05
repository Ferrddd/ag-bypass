# Antigravity Windows Uninstaller
[CmdletBinding()]
param()

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1. Check Administrator Privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] Ошибка: Для удаления требуются права Администратора." -ForegroundColor Red
    Write-Host "    Запустите PowerShell от имени Администратора (Run as Administrator)." -ForegroundColor Yellow
    exit 1
}

# 2. Deactivate bypass (ag off)
Write-Host "[*] Отключение маршрутов и удаление записей hosts..." -ForegroundColor Cyan
if (Test-Path "$ScriptDir\ag") {
    & python "$ScriptDir\ag" off
}

# 3. Restore original binary (ag unpatch)
Write-Host "[*] Восстановление оригинального бинарника Antigravity..." -ForegroundColor Cyan
if (Test-Path "$ScriptDir\ag") {
    & python "$ScriptDir\ag" unpatch
}

# 4. Remove Task Scheduler task
Write-Host "[*] Удаление задачи 'AntigravityBypassCheck' из Планировщика задач..." -ForegroundColor Cyan
& schtasks /delete /tn "AntigravityBypassCheck" /f 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[✓] Задача удалена из Планировщика задач." -ForegroundColor Green
} else {
    Write-Host "[i] Задача 'AntigravityBypassCheck' не была найдена в Планировщике." -ForegroundColor Gray
}

# 5. Clean CLOUD_CODE_URL from HKCU\Environment
Write-Host "[*] Очистка переменной CLOUD_CODE_URL из реестра..." -ForegroundColor Cyan
try {
    $regKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
    if ($regKey -and ($regKey.GetValue("CLOUD_CODE_URL") -ne $null)) {
        $regKey.DeleteValue("CLOUD_CODE_URL", $false)
        Write-Host "[✓] Значение CLOUD_CODE_URL удалено из HKCU\Environment." -ForegroundColor Green
    }
    if ($regKey) {
        $regKey.Close()
    }
} catch {
    Write-Host "[WARN] Не удалось удалить значение CLOUD_CODE_URL через Registry API: $_" -ForegroundColor Yellow
    & reg delete "HKCU\Environment" /v CLOUD_CODE_URL /f 2>$null
}

# 6. Remove ScriptDir from User Path
Write-Host "[*] Удаление $ScriptDir из переменной среды Path..." -ForegroundColor Cyan
$userPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
if ($userPath) {
    $pathParts = $userPath -split ";" | Where-Object { $_ -ne "" -and $_ -ne $ScriptDir }
    $newPath = $pathParts -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, [EnvironmentVariableTarget]::User)
    Write-Host "[✓] Каталог $ScriptDir удален из User Path." -ForegroundColor Green
}

Write-Host ""
Write-Host "[✓] Удаление Antigravity Bypass завершено." -ForegroundColor Green
