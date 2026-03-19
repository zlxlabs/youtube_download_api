# Stage 1: Build Python dependencies with uv
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
# --no-dev: exclude dev dependencies
# --no-hashes: simplify requirements.txt format
RUN uv export --no-dev --no-hashes -o requirements.txt && \
    uv pip install --system --no-cache --target=/app/deps -r requirements.txt

# Stage 2: Final runtime image
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies and JavaScript runtimes in one layer
# Runtime dependencies:
# - curl: healthcheck
# - ca-certificates: TLS verification
# - unzip: deno installation
# - libssl3: OpenSSL library for curl_cffi
# - libcurl4: curl library for curl_cffi
# - ffmpeg: audio transcoding (m4a/AAC)
# JavaScript runtimes:
# - Deno: for yt-dlp n challenge solving (nsig decryption)
# - Node.js: additional JS runtime support
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    unzip \
    libssl3 \
    libcurl4 \
    ffmpeg \
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

# Copy uv from builder stage (for runtime yt-dlp auto-update)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

# Copy Python dependencies from builder stage, then clean up unnecessary files
# This layer only rebuilds when pyproject.toml/uv.lock change
COPY --from=builder /app/deps /usr/local/lib/python3.11/site-packages/
RUN find /usr/local/lib/python3.11/site-packages -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type d -name "test" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type f -name "*.pyc" -delete 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type f -name "*.pyo" -delete 2>/dev/null || true && \
    rm -rf /usr/local/lib/python3.11/site-packages/setuptools* && \
    find /usr/local/lib/python3.11/site-packages -type d -name "docs" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type d -name "examples" -exec rm -rf {} + 2>/dev/null || true && \
    rm -rf /root/.cache && \
    rm -rf /tmp/*

# Create data directories (stable layer, rarely changes)
RUN mkdir -p /app/data/files/audio /app/data/files/transcript /app/data/logs

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

# --- Everything above this line is cached when only source code changes ---

# Copy source code (this is the layer that changes most frequently)
COPY src/ ./src/

# Inject build time via build arg -> env var (no file modification needed)
# Usage: docker build --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) .
# If not provided, defaults to "unknown"
ARG BUILD_TIME=unknown
ENV BUILD_TIME=${BUILD_TIME}

# PO Token Provider type: rust (default) or nodejs
# Controls which bgutil yt-dlp plugin to install at startup
ENV POT_PROVIDER_TYPE=rust

# Run the application with optional yt-dlp auto-update
# YTDLP_AUTO_UPDATE: update yt-dlp on startup (default: true)
# Set to "false" to disable auto-update
# This helps handle YouTube's frequent player.js changes
CMD ["sh", "-c", "\
    if [ \"${POT_PROVIDER_TYPE}\" = \"nodejs\" ]; then \
        POT_PKG='bgutil-ytdlp-pot-provider'; \
        POT_PKG_REMOVE='bgutil-ytdlp-pot-provider-rs'; \
    else \
        POT_PKG='bgutil-ytdlp-pot-provider-rs'; \
        POT_PKG_REMOVE='bgutil-ytdlp-pot-provider'; \
    fi && \
    echo \"[startup] PO Token Provider: ${POT_PROVIDER_TYPE} (package: ${POT_PKG})\" && \
    uv pip uninstall --system ${POT_PKG_REMOVE} 2>/dev/null || true && \
    if [ \"${POT_PROVIDER_TYPE}\" = \"nodejs\" ]; then \
        uv pip install --system --no-deps ${POT_PKG} 2>&1 \
        || echo \"[startup] ${POT_PKG} install failed\"; \
    else \
        uv pip install --system --no-deps \
            \"bgutil-ytdlp-pot-provider-rs @ git+https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs.git#subdirectory=plugin\" 2>&1 \
        || echo '[startup] bgutil-rs plugin install failed'; \
    fi && \
    if [ \"${YTDLP_AUTO_UPDATE:-true}\" = \"true\" ]; then \
        echo '[startup] Checking yt-dlp updates...' && \
        uv pip install --system --upgrade --no-deps yt-dlp yt-dlp-ejs 2>&1 \
        || echo '[startup] yt-dlp update failed, continuing with current version'; \
        echo \"[startup] yt-dlp version: $(python -c 'import yt_dlp; print(yt_dlp.version.__version__)')\"; \
    fi && \
    python -m uvicorn src.main:app --host 0.0.0.0 --port ${PORT}"]
