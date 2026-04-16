FROM python:3.11-slim

LABEL maintainer="isomer" \
      version="alpha" \
      description="Isomer — Compliance Tracking Platform"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data volume mount point
RUN mkdir -p /data/uploads

# Expose both ports
EXPOSE 27001 27000

# Persistent storage volume
VOLUME ["/data"]

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:27001/login')" || exit 1

# Entrypoint
CMD ["python", "entrypoint.py"]
