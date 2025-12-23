# GeoIP IP Range List Generator
# Generates downloadable IP range lists by continent and country

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmaxminddb0 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install Python dependencies
RUN pip install --no-cache-dir maxminddb

# Create non-root user
RUN groupadd -r geoip && useradd -r -g geoip geoip

# Set working directory
WORKDIR /app

# Copy application
COPY --chown=geoip:geoip generate_ip_lists.py /app/

# Create directories
RUN mkdir -p /output /geoip-data && chown -R geoip:geoip /output /geoip-data

# Switch to non-root user
USER geoip

# Environment variables
ENV GEOIP_DB_PATH=/geoip-data
ENV OUTPUT_PATH=/output
ENV GENERATION_INTERVAL=86400
ENV PYTHONUNBUFFERED=1

# Run the generator
CMD ["python", "generate_ip_lists.py"]
