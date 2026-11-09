# ==============================================================================
# AegisPay Backend Dockerfile - Lean Multi-Stage Python 3.12-slim
# ==============================================================================

# Stage 1: Build virtual environment and wheels
FROM python:3.12-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install -r requirements.txt

# Stage 2: Minimal runtime image
FROM python:3.12-slim AS runner

WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAYPAL_MODE=mock \
    DATABASE_URL=sqlite:////app/data/aegispay.db \
    PORT=8000

# Install curl for health check probe
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-built virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Create a non-root system user for security. /app/data holds the SQLite file; mount a volume
# there to keep state across restarts (a new named volume inherits this ownership).
RUN groupadd -g 1001 aegis && \
    useradd -u 1001 -g aegis -m -s /bin/bash aegisuser && \
    mkdir -p /app/data && \
    chown -R aegisuser:aegis /app

# Copy application code
COPY --chown=aegisuser:aegis backend/ /app/backend/
COPY --chown=aegisuser:aegis aegispay_mcp.json /app/aegispay_mcp.json

USER aegisuser

EXPOSE 8000

# Health check probing root /health endpoint
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Hosts such as Render inject PORT; listen on it.
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT}"]
