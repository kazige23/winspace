#requires -Version 5
<#
.SYNOPSIS
  Build the portable winspace release: PyInstaller -> dist/winspace -> zip.

.DESCRIPTION
  Runs from the repo root. Calls PyInstaller with packaging/winspace.spec,
  copies the README into the output folder, and produces a zip ready for
  GitHub Releases.

.EXAMPLE
  pwsh packaging/build-release.ps1
  pwsh packaging/build-release.ps1 -Version 0.1.0
#>

[CmdletBinding()]
param (
    [string] $Version = ""
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

$venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe" | Resolve-Path
if (-not (Test-Path $venvPython)) {
    throw "Virtualenv python not found at $venvPython. Run: python -m venv .venv && pip install -e .[dev]"
}

# Read version from version.py if not supplied
if (-not $Version) {
    $versionFile = Join-Path $PSScriptRoot "..\src\winspace\version.py" | Resolve-Path
    $line = Get-Content $versionFile | Where-Object { $_ -match '^__version__\s*=' } | Select-Object -First 1
    if ($line -match '"([^"]+)"') {
        $Version = $Matches[1]
    } else {
        $Version = "dev"
    }
}
Write-Host "Building winspace $Version" -ForegroundColor Cyan

# Clean previous artifacts so the build is reproducible
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

# Run PyInstaller
& $venvPython -m PyInstaller packaging/winspace.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

# Copy README into the dist folder
$distDir = Join-Path (Get-Location) "dist\winspace"
$readme = Join-Path $PSScriptRoot "portable\README.txt"
Copy-Item $readme -Destination $distDir -Force

# Show what we built
$files = Get-ChildItem $distDir -Recurse -File
$total = ($files | Measure-Object Length -Sum).Sum
Write-Host ("Built {0} files, {1:N1} MB total" -f $files.Count, ($total/1MB)) -ForegroundColor Green

# Zip it up
$zipName = "winspace-$Version-windows-x64.zip"
$zipPath = Join-Path (Get-Location) "dist\$zipName"
if (Test-Path $zipPath) { Remove-Item $zipPath }
Compress-Archive -Path $distDir -DestinationPath $zipPath -CompressionLevel Optimal

$zipSize = (Get-Item $zipPath).Length / 1MB
Write-Host ("Wrote {0} ({1:N1} MB)" -f $zipPath, $zipSize) -ForegroundColor Green
Write-Host "" -ForegroundColor Green
Write-Host "Release artifact ready at: $zipPath" -ForegroundColor Yellow
