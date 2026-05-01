# build.ps1
# Rebuild the RebateTracker standalone distribution.
# Run from the project root:  .\build.ps1

Set-Location $PSScriptRoot

Write-Host "==> Cleaning previous build..." -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Running PyInstaller..." -ForegroundColor Cyan
C:\rtenv\Scripts\pyinstaller rebate_tracker.spec

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build FAILED." -ForegroundColor Red
    exit 1
}

Write-Host "==> Copying release notes..." -ForegroundColor Cyan
Copy-Item "dist\RebateTracker\README_DISTRIBUTION.txt" "dist\RebateTracker\" -ErrorAction SilentlyContinue

Write-Host "==> Creating zip archive..." -ForegroundColor Cyan
$zipPath = "dist\RebateTracker_$(Get-Date -Format 'yyyyMMdd').zip"
Compress-Archive -Path "dist\RebateTracker\*" -DestinationPath $zipPath -Force

Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "Distributable: $zipPath" -ForegroundColor Green
Write-Host "Folder:        dist\RebateTracker\" -ForegroundColor Green
