# Development startup script for Windows
# Usage: .\scripts\dev.ps1

$ErrorActionPreference = "Stop"

Write-Host "Starting YouTube Audio API development environment..." -ForegroundColor Cyan

# Check if uv is installed
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..." -ForegroundColor Yellow
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}

# Sync dependencies (creates .venv automatically if not exists)
Write-Host "Syncing dependencies with uv..." -ForegroundColor Yellow
uv sync

# Check if .env.development exists
if (-not (Test-Path ".\.env.development")) {
    Write-Host "Creating .env.development from template..." -ForegroundColor Yellow
    Copy-Item ".\.env.example" ".\.env.development"
    Write-Host "Please edit .env.development with your configuration" -ForegroundColor Red
    exit 1
}

# Start pot-provider container
Write-Host "Starting pot-provider container..." -ForegroundColor Yellow
docker-compose -f docker-compose.dev.yml up -d

# Wait for pot-provider to be ready
Write-Host "Waiting for pot-provider to be ready..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# Check pot-provider health
try {
    $response = Invoke-WebRequest -Uri "http://localhost:4416/health" -TimeoutSec 5
    Write-Host "pot-provider is ready" -ForegroundColor Green
} catch {
    Write-Host "Warning: pot-provider may not be ready yet" -ForegroundColor Yellow
}

# Set environment file
$env:ENV_FILE = ".env.development"

# Start the development server
Write-Host "Starting development server..." -ForegroundColor Green
uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
