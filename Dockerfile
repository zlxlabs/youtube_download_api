# Stage 1: Download ffmpeg static binary
FROM alpine:3.19 AS ffmpeg-downloader

RUN apk add --no-cache curl tar xz

# Download ffmpeg LGPL static build from BtbN (smaller than GPL version)
# https://github.com/BtbN/FFmpeg-Builds
# LGPL version is sufficient for audio transcoding (m4a/AAC)
# GPL version (~386MB) vs LGPL version (~140MB)
RUN curl -L --retry 3 --retry-delay 5 --retry-connrefused \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-lgpl.tar.xz" \
    -o /tmp/ffmpeg.tar.xz && \
    mkdir -p /tmp/ffmpeg && \
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg --strip-components=1 && \
    cp /tmp/ffmpeg/bin/ffmpeg /tmp/ffmpeg/bin/ffprobe /usr/local/bin/

# Stage 2: Build Python dependencies with uv
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency files (uv.lock is required for uv export)
# README.md is required by hatchling to build the local package
COPY pyproject.toml uv.lock README.md ./

# Export dependencies to requirements.txt and install
# --no-dev: 排除开发依赖
# --no-hashes: 简化 requirements.txt 格式
RUN uv export --no-dev --no-hashes -o requirements.txt && \
    uv pip install --system --no-cache --target=/app/deps -r requirements.txt

# Stage 3: Final runtime image
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies and JavaScript runtimes in one layer
# Runtime dependencies:
# - curl: healthcheck
# - ca-certificates: TLS verification
# - unzip: deno installation
# - libssl3: OpenSSL library for curl_cffi
# - libcurl4: curl library for curl_cffi
# JavaScript runtimes:
# - Deno: for yt-dlp n challenge solving (nsig decryption)
# - Node.js: additional JS runtime support
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    unzip \
    libssl3 \
    libcurl4 \
    # Install Deno
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version \
    # Install Node.js from NodeSource
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && node --version \
    # Clean up to reduce image size
    && rm -rf /usr/lib/node_modules/npm \
    && rm -rf /usr/lib/node_modules/corepack \
    && rm -rf /usr/share/doc/nodejs \
    && rm -rf /usr/share/man/man1/node* \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /tmp/* \
    && rm -rf /root/.cache

# Copy ffmpeg binaries from downloader stage
COPY --from=ffmpeg-downloader /usr/local/bin/ffmpeg /usr/local/bin/
COPY --from=ffmpeg-downloader /usr/local/bin/ffprobe /usr/local/bin/

# Copy Python dependencies from builder stage
COPY --from=builder /app/deps /usr/local/lib/python3.11/site-packages/

# Copy source code
COPY src/ ./src/

# 注入构建时间到 __init__.py
# 使用 ISO 8601 格式的 UTC 时间戳
RUN BUILD_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ") && \
    sed -i "s/BUILD_TIMESTAMP_PLACEHOLDER/${BUILD_TIME}/g" ./src/__init__.py && \
    echo "Build time injected: ${BUILD_TIME}"

# Create data directories
RUN mkdir -p /app/data/files/audio /app/data/files/transcript /app/data/logs

# Remove unnecessary files to reduce size
RUN find /usr/local/lib/python3.11/site-packages -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type d -name "test" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type f -name "*.pyc" -delete 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type f -name "*.pyo" -delete 2>/dev/null || true && \
    rm -rf /usr/local/lib/python3.11/site-packages/pip* && \
    rm -rf /usr/local/lib/python3.11/site-packages/setuptools* && \
    # 移除 Python 包中的文档和示例
    find /usr/local/lib/python3.11/site-packages -type d -name "docs" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type d -name "examples" -exec rm -rf {} + 2>/dev/null || true && \
    # 清理所有缓存
    rm -rf /root/.cache && \
    rm -rf /tmp/*

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DATA_DIR=/app/data

# Default port (can be overridden by PORT env var)
ENV PORT=8000

# Expose port
EXPOSE ${PORT}

# Health check (uses PORT env var)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Run the application (shell form to expand PORT env var)
CMD ["sh", "-c", "python -m uvicorn src.main:app --host 0.0.0.0 --port ${PORT}"]
