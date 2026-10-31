#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Antigravity Windows Bypass — Standalone PowerShell Edition
    Runs on pure Windows without requiring Python.

.EXAMPLE
    .\ag.ps1 on
    .\ag.ps1 status
    .\ag.ps1 nodes
    .\ag.ps1 check
    .\ag.ps1 off
    .\ag.ps1 unpatch
#>

param(
    [Parameter(Position=0)]
    [ValidateSet("on", "off", "status", "nodes", "check", "unpatch", "help")]
    [string]$Command = "help",

    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigFile = Join-Path $ScriptDir "config.json"

$TargetDomain = "daily-cloudcode-pa.googleapis.com"
$SmartDnsHost = "dns.geohide.ru"
$SmartDnsIp = "45.155.204.190"
$ProbePort = 443
$DefaultNodes = @("159.194.200.33", "193.233.112.67", "193.233.112.68", "193.233.112.88")

if (Test-Path $ConfigFile) {
    try {
        $cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        if ($cfg.domains -and $cfg.domains.Count -gt 0) { $TargetDomain = $cfg.domains[0] }
        if ($cfg.smart_dns.fallback_ip) { $SmartDnsIp = $cfg.smart_dns.fallback_ip }
        if ($cfg.smart_dns.host) { $SmartDnsHost = $cfg.smart_dns.host }
        if ($cfg.healthcheck.probe_port) { $ProbePort = $cfg.healthcheck.probe_port }
    } catch {}
}

$HostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
$TagBegin = "# BEGIN AG_BYPASS"
$TagEnd = "# END AG_BYPASS"

function Test-IsAdmin {
    return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Require-Admin ($action) {
    if (-not (Test-IsAdmin)) {
        Write-Host "[!] Error: Administrator rights required for command '$action'!" -ForegroundColor Red
        Write-Host "    Please run PowerShell as Administrator." -ForegroundColor Yellow
        Exit 1
    }
}

function Find-AgyBinary {
    $cands = @(
        "$env:USERPROFILE\.local\bin\agy.exe",
        "$env:LOCALAPPDATA\Programs\agy\agy.exe",
        "$env:LOCALAPPDATA\agy\agy.exe",
        "$env:LOCALAPPDATA\Google\Antigravity\agy.exe",
        "$env:APPDATA\agy\bin\agy.exe",
        "$env:ProgramFiles\Google\Antigravity\agy.exe",
        "${env:ProgramFiles(x86)}\Google\Antigravity\agy.exe"
    )
    $inPath = (Get-Command "agy.exe" -ErrorAction SilentlyContinue).Source
    if ($inPath) { $cands += $inPath }

    foreach ($c in $cands) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

function Test-TlsNode ($ip, $domain, $port = 443, $timeoutMs = 2500) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $tcp = $null
    $ssl = $null
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $connectTask = $tcp.ConnectAsync($ip, $port)
        if (-not $connectTask.Wait($timeoutMs)) {
            $tcp.Close()
            return [PSCustomObject]@{ Ip=$ip; Ok=$false; Latency=0; Detail="Connection Timeout" }
        }
        $stream = $tcp.GetStream()
        $stream.ReadTimeout = $timeoutMs
        $stream.WriteTimeout = $timeoutMs

        # SSL handshake with SNI
        $callback = { $true }
        $ssl = New-Object System.Net.Security.SslStream($stream, $false, $callback)
        $ssl.AuthenticateAsClient($domain)
        $sw.Stop()

        # Send quick HTTP HEAD
        $req = [System.Text.Encoding]::ASCII.GetBytes("HEAD / HTTP/1.1`r`nHost: $domain`r`nConnection: close`r`n`r`n")
        $ssl.Write($req, 0, $req.Length)
        $buf = New-Object byte[] 128
        $bytesRead = $ssl.Read($buf, 0, $buf.Length)
        $statusLine = if ($bytesRead -gt 0) { [System.Text.Encoding]::ASCII.GetString($buf, 0, $bytesRead).Split("`r`n")[0] } else { "Empty response" }

        return [PSCustomObject]@{
            Ip = $ip
            Ok = $true
            Latency = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
            Detail = $statusLine
        }
    } catch {
        return [PSCustomObject]@{ Ip=$ip; Ok=$false; Latency=0; Detail=$_.Exception.Message }
    } finally {
        if ($ssl) { $ssl.Dispose() }
        if ($tcp) { $tcp.Dispose() }
    }
}

function Get-SmartDnsNodes {
    $nodes = @()
    try {
        $dnsRes = Resolve-DnsName -Server $SmartDnsIp -Name $TargetDomain -Type A -DnsOnly -QuickTimeout -ErrorAction SilentlyContinue
        if ($dnsRes) {
            $nodes = $dnsRes | Where-Object { $_.IPAddress } | ForEach-Object { $_.IPAddress }
        }
    } catch {}
    if ($nodes.Count -eq 0) {
        $nodes = $DefaultNodes
    }
    return $nodes | Select-Object -Unique
}

function Get-ActiveHostsIp {
    if (-not (Test-Path $HostsPath)) { return $null }
    $content = Get-Content $HostsPath -Raw -ErrorAction SilentlyContinue
    if ($content -match "$TagBegin\s*([\d\.]+)\s+$TargetDomain") {
        return $matches[1]
    }
    return $null
}

function Set-HostsIp ($ip) {
    $content = if (Test-Path $HostsPath) { Get-Content $HostsPath -Raw } else { "" }
    $clean = [regex]::Replace($content, "$TagBegin.*?$TagEnd\r?\n?", "", [System.Text.RegularExpressions.RegexOptions]::Singleline).TrimEnd()
    $block = "`r`n$TagBegin`r`n$ip $TargetDomain`r`n$TagEnd`r`n"
    $newContent = $clean + $block

    Set-ItemProperty $HostsPath -Name IsReadOnly -Value $false -ErrorAction SilentlyContinue
    [System.IO.File]::WriteAllText($HostsPath, $newContent, [System.Text.Encoding]::UTF8)
    ipconfig /flushdns | Out-Null
}

function Clear-HostsEntries {
    if (-not (Test-Path $HostsPath)) { return }
    $content = Get-Content $HostsPath -Raw
    if ($content -match $TagBegin) {
        $clean = [regex]::Replace($content, "$TagBegin.*?$TagEnd\r?\n?", "", [System.Text.RegularExpressions.RegexOptions]::Singleline).TrimEnd()
        Set-ItemProperty $HostsPath -Name IsReadOnly -Value $false -ErrorAction SilentlyContinue
        [System.IO.File]::WriteAllText($HostsPath, ($clean + "`r`n"), [System.Text.Encoding]::UTF8)
        ipconfig /flushdns | Out-Null
    }
}

function Patch-Binary ($binPath) {
    $pids = Get-Process -Name (([System.IO.Path]::GetFileNameWithoutExtension($binPath))) -ErrorAction SilentlyContinue
    if ($pids) {
        Write-Host "      [WARN] Process agy is currently running. Please close it first." -ForegroundColor Yellow
        return $false
    }
    $bytes = [System.IO.File]::ReadAllBytes($binPath)
    $sigOrig = [System.Text.Encoding]::ASCII.GetBytes("ineligible")
    $sigPatch = [System.Text.Encoding]::ASCII.GetBytes("inexigible")

    # Byte scan without converting whole file to string
    $foundOrig = $false
    $foundPatch = $false

    for ($i = 0; $i -le ($bytes.Length - 10); $i++) {
        $matchedOrig = $true
        $matchedPatch = $true
        for ($j = 0; $j -lt 10; $j++) {
            if ($bytes[$i + $j] -ne $sigOrig[$j]) { $matchedOrig = $false }
            if ($bytes[$i + $j] -ne $sigPatch[$j]) { $matchedPatch = $false }
        }
        if ($matchedPatch) { $foundPatch = $true; break }
        if ($matchedOrig) { $foundOrig = $true }
    }

    if ($foundPatch) {
        Write-Host "      Client binary is ALREADY PATCHED (inexigible found)." -ForegroundColor Green
        return $true
    }
    if (-not $foundOrig) {
        Write-Host "      [!] Signature 'ineligible' not found in binary." -ForegroundColor Yellow
        return $false
    }

    $bak = "$binPath.bak"
    if (-not (Test-Path $bak)) {
        Copy-Item $binPath $bak
    }

    # Replace bytes
    for ($i = 0; $i -le ($bytes.Length - 10); $i++) {
        $m = $true
        for ($j = 0; $j -lt 10; $j++) {
            if ($bytes[$i + $j] -ne $sigOrig[$j]) { $m = $false; break }
        }
        if ($m) {
            for ($j = 0; $j -lt 10; $j++) {
                $bytes[$i + $j] = $sigPatch[$j]
            }
        }
    }
    [System.IO.File]::WriteAllBytes($binPath, $bytes)
    Write-Host "      [+] Binary patched successfully! Backup saved to $bak" -ForegroundColor Green
    return $true
}

switch ($Command) {
    "on" {
        Require-Admin "on"
        Write-Host ""
        Write-Host "=== Activating Antigravity Windows Bypass (PowerShell) ===" -ForegroundColor Cyan
        Write-Host ""

        # 1. Binary Patch
        Write-Host "[1/5] Checking Antigravity client..." -ForegroundColor White
        $bin = Find-AgyBinary
        if ($bin) {
            Patch-Binary $bin | Out-Null
        } else {
            Write-Host "      [WARN] agy.exe not found in standard directories." -ForegroundColor Yellow
        }

        # 2. Endpoint variable
        Write-Host "[2/5] Setting CLOUD_CODE_URL..." -ForegroundColor White
        [Environment]::SetEnvironmentVariable("CLOUD_CODE_URL", "https://$TargetDomain", "User")
        Write-Host "      [+] Set CLOUD_CODE_URL=https://$TargetDomain (User Environment)" -ForegroundColor Green

        # 3. DNS resolve
        Write-Host "[3/5] Querying SmartDNS ($SmartDnsIp)..." -ForegroundColor White
        $nodes = Get-SmartDnsNodes
        Write-Host "      Candidate nodes: $($nodes -join ', ')"

        # 4. TLS check & benchmark
        Write-Host "[4/5] Testing TLS latency on port $ProbePort..." -ForegroundColor White
        $results = @()
        foreach ($node in $nodes) {
            $results += Test-TlsNode -ip $node -domain $TargetDomain -port $ProbePort
        }
        $alive = $results | Where-Object { $_.Ok } | Sort-Object Latency
        if (-not $alive -or $alive.Count -eq 0) {
            Write-Host "      [ERR] None of the proxy nodes answered TLS handshake!" -ForegroundColor Red
            Exit 1
        }
        $best = $alive[0]
        Write-Host "      [+] Best node selected: $($best.Ip) (ping: $($best.Latency) ms)" -ForegroundColor Green

        # 5. Hosts update
        Write-Host "[5/5] Updating hosts file ($HostsPath)..." -ForegroundColor White
        Set-HostsIp $best.Ip
        Write-Host "      [+] $($best.Ip) -> $TargetDomain added to hosts" -ForegroundColor Green

        Write-Host ""
        Write-Host "[+] Bypass successfully activated! Antigravity is ready to use without VPN." -ForegroundColor Green
        Write-Host ""
    }

    "off" {
        Require-Admin "off"
        Clear-HostsEntries
        [Environment]::SetEnvironmentVariable("CLOUD_CODE_URL", $null, "User")
        Write-Host "[+] Bypass disabled: hosts entries cleared, CLOUD_CODE_URL removed." -ForegroundColor Green
    }

    "status" {
        Write-Host ""
        Write-Host "=== Antigravity Windows Bypass Status ===" -ForegroundColor Cyan
        Write-Host ""
        $bin = Find-AgyBinary
        if ($bin) {
            $bytes = [System.IO.File]::ReadAllBytes($bin)
            $text = [System.Text.Encoding]::ASCII.GetString($bytes, 0, [Math]::Min($bytes.Length, 10000000))
            $st = if ($text.Contains("inexigible")) { "ALREADY_PATCHED" } else { "READY_TO_PATCH" }
            Write-Host "1. Client:     $st ($bin)" -ForegroundColor Green
        } else {
            Write-Host "1. Client:     NOT FOUND" -ForegroundColor Yellow
        }

        $curEnv = [Environment]::GetEnvironmentVariable("CLOUD_CODE_URL", "User")
        $envDesc = if ($curEnv) { "ACTIVE ($curEnv)" } else { "NOT CONFIGURED" }
        Write-Host "2. Endpoint:   $envDesc" -ForegroundColor $(if ($curEnv) { "Green" } else { "Yellow" })

        $curIp = Get-ActiveHostsIp
        if ($curIp) {
            Write-Host "3. Routing:    ACTIVE ($curIp -> $TargetDomain)" -ForegroundColor Green
            $check = Test-TlsNode -ip $curIp -domain $TargetDomain -port $ProbePort
            $statusWord = if ($check.Ok) { "ONLINE" } else { "OFFLINE" }
            Write-Host "4. Active IP:  $curIp ($statusWord, $($check.Latency) ms, $($check.Detail))" -ForegroundColor $(if ($check.Ok) { "Green" } else { "Red" })
        } else {
            Write-Host "3. Routing:    DISABLED (hosts is clean)" -ForegroundColor Yellow
            Write-Host "4. Active IP:  No active entry in hosts" -ForegroundColor Gray
        }

        $task = Get-ScheduledTask -TaskName "AntigravityBypass" -ErrorAction SilentlyContinue
        $taskDesc = if ($task) { "ACTIVE ($($task.State))" } else { "NOT INSTALLED" }
        Write-Host "5. Scheduler:  $taskDesc" -ForegroundColor $(if ($task) { "Green" } else { "Yellow" })
        Write-Host ""
    }

    "nodes" {
        Write-Host ""
        Write-Host "Testing available proxy nodes..." -ForegroundColor Cyan
        Write-Host ""
        $nodes = Get-SmartDnsNodes
        $curIp = Get-ActiveHostsIp
        $results = @()
        foreach ($node in $nodes) {
            $check = Test-TlsNode -ip $node -domain $TargetDomain -port $ProbePort
            $results += [PSCustomObject]@{
                IP = $node
                Status = if ($check.Ok) { "ONLINE" } else { "OFFLINE" }
                Latency = if ($check.Ok) { "$($check.Latency) ms" } else { "-" }
                Active = if ($node -eq $curIp) { "* YES" } else { "  -" }
                Detail = $check.Detail
            }
        }
        $results | Format-Table -AutoSize
    }

    "check" {
        $curIp = Get-ActiveHostsIp
        if (-not $curIp) {
            if (-not $Quiet) { Write-Host "Bypass is not active in hosts" -ForegroundColor Yellow }
            return
        }
        $check = Test-TlsNode -ip $curIp -domain $TargetDomain -port $ProbePort
        if ($check.Ok) {
            if (-not $Quiet) { Write-Host "[HEALTHY] Current node $curIp is responding ($($check.Latency) ms)" -ForegroundColor Green }
            return
        }
        if (-not $Quiet) { Write-Host "[!] Node $curIp failed. Searching for backup node..." -ForegroundColor Yellow }
        $nodes = Get-SmartDnsNodes
        $alive = @()
        foreach ($node in $nodes) {
            $t = Test-TlsNode -ip $node -domain $TargetDomain -port $ProbePort
            if ($t.Ok) { $alive += $t }
        }
        $sorted = $alive | Sort-Object Latency
        if ($sorted.Count -gt 0) {
            $newBest = $sorted[0]
            Set-HostsIp $newBest.Ip
            if (-not $Quiet) { Write-Host "[HEALED] Switched from $curIp to $($newBest.Ip) ($($newBest.Latency) ms)" -ForegroundColor Green }
        } else {
            if (-not $Quiet) { Write-Host "[FAILED] None of the backup nodes responded" -ForegroundColor Red }
        }
    }

    "unpatch" {
        Require-Admin "unpatch"
        $bin = Find-AgyBinary
        if (-not $bin) { Write-Host "[ERR] agy.exe not found" -ForegroundColor Red; Exit 1 }
        $bak = "$bin.bak"
        if (Test-Path $bak) {
            Copy-Item $bak $bin -Force
            Remove-Item $bak -Force
            Write-Host "[+] Binary successfully restored from backup." -ForegroundColor Green
        } else {
            Write-Host "[!] Backup not found: $bak" -ForegroundColor Yellow
        }
    }

    Default {
        Write-Host @"
Usage: .\ag.ps1 [command]

Commands:
  on       - Enable bypass: patch agy.exe, select fastest IP and write to hosts
  off      - Disable bypass: clean hosts and remove environment variables
  status   - Show status of client, active node, latency and scheduler
  nodes    - Test all available nodes and display latency table
  check    - Check health of current node and auto-failover if down
  unpatch  - Restore original agy.exe from backup
"@
    }
}
