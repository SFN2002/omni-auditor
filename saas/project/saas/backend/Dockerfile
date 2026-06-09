# Omni-Auditor SaaS Dashboard — Backend Dockerfile
# Multi-stage build for production optimization

# ── Stage 1: Builder ──────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY saas/backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Production ───────────────────────────────────────
FROM python:3.11-slim AS production

WORKDIR /app

# Create non-root user
RUN groupadd -r omniuser \
    && useradd -r -g omniuser -s /bin/false omniuser

# Install runtime dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY saas/backend /app/saas/backend
COPY saas/__init__.py /app/saas/__init__.py
COPY saas/backend/__init__.py /app/saas/backend/__init__.py
COPY saas/backend/api/__init__.py /app/saas/backend/api/__init__.py

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    WORKERS=4

# Switch to non-root user
USER omniuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" \
    || exit 1

# Run with uvicorn
CMD uvicorn saas.backend.main:app \
    --host 0.0.0.0 \
    --port ${PORT} \
    --workers ${WORKERS} \
    --proxy-headers \
    --forwarded-allow-ips *
