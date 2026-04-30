# Use official Python slim image
FROM python:3.12-slim-bookworm AS builder

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.12-slim-bookworm

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    KNOWN_HOSTS_FILE=/app/data/known_hosts

WORKDIR /app

# Create a non-root user
RUN groupadd -g 10001 botgroup && \
    useradd -u 10001 -g botgroup -m -s /bin/bash botuser && \
    mkdir -p /app/data && \
    chown -R botuser:botgroup /app/data

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copy application code with proper ownership
COPY --chown=botuser:botgroup . .

# Switch to non-root user
USER botuser

# Healthcheck to monitor FastAPI application
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Command to run the bot
CMD ["python", "main.py"]
