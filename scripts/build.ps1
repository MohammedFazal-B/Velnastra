# Build from any working directory using the project root as Docker context.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$dockerfile = Join-Path $projectRoot "Dockerfile"

Write-Host "Building prompt-firewall:gpu..." -ForegroundColor Cyan
docker build --tag prompt-firewall:gpu --file $dockerfile $projectRoot

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed. Check the errors above." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "Build successful. From the project root, run: docker compose up -d" -ForegroundColor Green
