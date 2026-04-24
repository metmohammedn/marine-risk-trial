FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies (none needed for this app, but keep layer for future)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Default port — matches config.py default
EXPOSE 8050

# Health check using the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8050/health || exit 1

# Run with gunicorn for production
# - 4 workers (adjust via GUNICORN_WORKERS env var)
# - Bind to 0.0.0.0:8050 (override via HOST/PORT env vars)
CMD ["sh", "-c", \
    "gunicorn app:server \
        --bind ${HOST:-0.0.0.0}:${PORT:-8050} \
        --workers ${GUNICORN_WORKERS:-4} \
        --timeout ${GUNICORN_TIMEOUT:-120} \
        --access-logfile - \
        --error-logfile -"]
