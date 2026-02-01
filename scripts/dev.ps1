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
# --prerelease=allow: 允许安装预发布版本（yt-dlp 需要）
Write-Host "Syncing dependencies with uv..." -ForegroundColor Yellow
uv sync --prerelease=allow

# Check if .env exists
if (-not (Test-Path ".\.env")) {
    Write-Host "Creating .env from template..." -ForegroundColor Yellow
    Copy-Item ".\.env.example" ".\.env"
    Write-Host "Please edit .env with your configuration" -ForegroundColor Red
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

# Load .env file and start the development server
Write-Host "Starting development server..." -ForegroundColor Green

# Load environment variables from .env file
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#].+?)=(.*)$') {
        $name = $matches[1].Trim()
        $value = $matches[2].Trim()
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

# Get port from environment or use default
$port = if ($env:PORT) { $env:PORT } else { "8011" }

# Windows 下 uvicorn --reload 使用 SelectorEventLoop，不支持子进程
# 这会导致 Playwright (CDP) 无法启动，因此 Windows 上不使用 --reload
uv run uvicorn src.main:app --host 127.0.0.1 --port $port
