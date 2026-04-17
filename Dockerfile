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

# Expose the single application port
EXPOSE 27001

# Persistent storage volume
VOLUME ["/data"]

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:27001/login')" || exit 1

# entrypoint.py creates /data/uploads; gunicorn then serves the app.
# Two workers is plenty for this traffic; threads=4 keeps I/O bound handlers
# (zip export, evidence serving) from blocking each other.
CMD ["sh", "-c", "python entrypoint.py && exec gunicorn --workers 2 --threads 4 --bind 0.0.0.0:27001 --access-logfile - --error-logfile - app:app"]
