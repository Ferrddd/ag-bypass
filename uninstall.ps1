#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Удаление Antigravity Windows Bypass
#>

$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "        Удаление Antigravity Windows Bypass" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Проверка прав администратора
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] Ошибка: Для удаления требуются права Администратора!" -ForegroundColor Red
    Exit 1
}

# 2. Отключение обхода и восстановление бинарника
$agScript = Join-Path $ScriptDir "ag.py"
if (Test-Path $agScript) {
    Write-Host "[*] Отключение записей в hosts и очистка переменных..." -ForegroundColor Cyan
    python "$agScript" off
    python "$agScript" unpatch
}

# 3. Удаление задания из Планировщика задач
Write-Host "[*] Удаление задачи из Планировщика Windows..." -ForegroundColor Cyan
cmd /c "schtasks /delete /tn `"AntigravityBypass`" /f" 2>&1 | Out-Null
Write-Host "[+] Задача AntigravityBypass удалена из Планировщика." -ForegroundColor Green

# 4. Удаление из PATH
$currentPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
if ($currentPath -like "*$ScriptDir*") {
    $newPath = ($currentPath.Split(";") | Where-Object { $_ -ne $ScriptDir -and $_ -ne "" }) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, [EnvironmentVariableTarget]::User)
    Write-Host "[+] Путь к скрипту удален из переменной PATH пользователя." -ForegroundColor Green
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "[+] Удаление полностью завершено." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan

Write-Host ""
Read-Host "Нажмите Enter для завершения..."
