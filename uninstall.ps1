param(
    [switch]$RemoveData
)

$ErrorActionPreference = "Stop"
$InstallDirectory = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "GitHubPulse"
$ExpectedDirectory = [IO.Path]::GetFullPath($InstallDirectory)
$ProgramsDirectory = [Environment]::GetFolderPath("Programs")
$ShortcutPath = Join-Path $ProgramsDirectory "GitHub Pulse.lnk"

if ([IO.Path]::GetFileName($ExpectedDirectory) -ne "GitHubPulse") {
    throw "Refusing to uninstall from an unexpected directory: $ExpectedDirectory"
}

if (Test-Path -LiteralPath $ShortcutPath) {
    Remove-Item -LiteralPath $ShortcutPath -Force
}

$installedFiles = @("app.py", "start.ps1", "start.cmd", "README.md", "LICENSE", "uninstall.ps1")
foreach ($file in $installedFiles) {
    $target = Join-Path $ExpectedDirectory $file
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        Remove-Item -LiteralPath $target -Force
    }
}

$StaticDirectory = Join-Path $ExpectedDirectory "static"
if (Test-Path -LiteralPath $StaticDirectory -PathType Container) {
    Remove-Item -LiteralPath $StaticDirectory -Recurse -Force
}

$DataDirectory = Join-Path $ExpectedDirectory "data"
if ($RemoveData -and (Test-Path -LiteralPath $DataDirectory -PathType Container)) {
    Remove-Item -LiteralPath $DataDirectory -Recurse -Force
}

if (Test-Path -LiteralPath $ExpectedDirectory -PathType Container) {
    $RemainingItems = Get-ChildItem -LiteralPath $ExpectedDirectory -Force
    if (-not $RemainingItems) {
        Remove-Item -LiteralPath $ExpectedDirectory -Force
    }
}

Write-Host ""
Write-Host "GitHub Pulse has been uninstalled." -ForegroundColor Green
if (-not $RemoveData -and (Test-Path -LiteralPath $DataDirectory)) {
    Write-Host "Local history was preserved in $DataDirectory"
    Write-Host "Delete that folder manually only if you also want to remove the collected history."
}
