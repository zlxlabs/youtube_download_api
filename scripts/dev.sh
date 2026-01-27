#!/bin/bash
# Development startup script for Linux/Mac
# Usage: ./scripts/dev.sh

set -e

echo -e "\033[36mStarting YouTube Audio API development environment...\033[0m"

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo -e "\033[33mInstalling uv...\033[0m"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for current session
    export PATH="$HOME/.local/bin:$PATH"
fi

# Sync dependencies (creates .venv automatically if not exists)
# --prerelease=allow: 允许安装预发布版本（yt-dlp 需要）
echo -e "\033[33mSyncing dependencies with uv...\033[0m"
uv sync --prerelease=allow

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "\033[33mCreating .env from template...\033[0m"
    cp .env.example .env
    echo -e "\033[31mPlease edit .env with your configuration\033[0m"
    exit 1
fi

# Start pot-provider container
echo -e "\033[33mStarting pot-provider container...\033[0m"
docker-compose -f docker-compose.dev.yml up -d

# Wait for pot-provider to be ready
echo -e "\033[33mWaiting for pot-provider to be ready...\033[0m"
sleep 5

# Check pot-provider health
if curl -s -f http://localhost:4416/health > /dev/null 2>&1; then
    echo -e "\033[32mpot-provider is ready\033[0m"
else
    echo -e "\033[33mWarning: pot-provider may not be ready yet\033[0m"
fi

# Start the development server
echo -e "\033[32mStarting development server...\033[0m"
uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
