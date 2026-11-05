[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "[!] Python 3 not found in PATH. Please install Python 3."
    exit 1
}

& python "$ScriptDir\ag" @Arguments
exit $LASTEXITCODE
