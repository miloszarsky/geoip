# GeoIP API Service Dockerfile
# Multi-stage build for optimized production image

# Stage 1: Build dependencies
FROM python:3.12-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libmaxminddb0 \
    libmaxminddb-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /build/wheels -r requirements.txt

# Stage 2: Production image
FROM python:3.12-slim

# Create non-root user for security
RUN groupadd -r geoip && useradd -r -g geoip geoip

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmaxminddb0 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy wheels from builder and install
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy application code
COPY --chown=geoip:geoip main.py .

# Create directory for GeoIP databases
RUN mkdir -p /geoip-data && chown geoip:geoip /geoip-data

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GEOIP_DATA_DIR=/geoip-data \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    API_LOG_LEVEL=info

# Switch to non-root user
USER geoip

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start the API server with proxy headers support
# --proxy-headers: Trust X-Forwarded-* headers
# --forwarded-allow-ips: IPs allowed to set forwarded headers (Docker networks)
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
