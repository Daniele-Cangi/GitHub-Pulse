$ErrorActionPreference = "Stop"
$AppDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $AppDirectory

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) non è disponibile nel PATH."
}

python .\app.py
