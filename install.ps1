param(
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
$SourceDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallDirectory = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "GitHubPulse"
$StaticDirectory = Join-Path $InstallDirectory "static"
$DataDirectory = Join-Path $InstallDirectory "data"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.10 or newer is required and must be available in PATH."
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required and must be available in PATH."
}

$requiredFiles = @("app.py", "start.ps1", "start.cmd", "README.md", "LICENSE", "uninstall.ps1")
foreach ($file in $requiredFiles) {
    $source = Join-Path $SourceDirectory $file
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required installation file is missing: $file"
    }
}

New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $StaticDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null

foreach ($file in $requiredFiles) {
    Copy-Item -LiteralPath (Join-Path $SourceDirectory $file) -Destination (Join-Path $InstallDirectory $file) -Force
}
Get-ChildItem -LiteralPath (Join-Path $SourceDirectory "static") -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $StaticDirectory $_.Name) -Force
}

if (-not $NoShortcut) {
    $ProgramsDirectory = [Environment]::GetFolderPath("Programs")
    $ShortcutPath = Join-Path $ProgramsDirectory "GitHub Pulse.lnk"
    $PowerShellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $PowerShellPath
    $Shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $InstallDirectory "start.ps1") + '"'
    $Shortcut.WorkingDirectory = $InstallDirectory
    $Shortcut.Description = "Open the local GitHub Pulse dashboard"
    $Shortcut.Save()
}

Write-Host ""
Write-Host "GitHub Pulse is installed in $InstallDirectory" -ForegroundColor Green
Write-Host "Your local history will be stored in $DataDirectory"
if (-not $NoShortcut) {
    Write-Host "Open GitHub Pulse from the Windows Start menu."
} else {
    Write-Host "Run .\start.ps1 from the installation directory."
}
