FROM python:3.11-slim

WORKDIR /app

# System packages needed by Playwright/Chromium
# (playwright install --with-deps handles most, but we need apt certs first)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser + all its system library dependencies
# (~350 MB; runs inside the image so no host browser needed)
RUN playwright install --with-deps chromium

# Copy source code
COPY . .

# Persistent data directory (DB + logs).  Override with DATA_DIR env var.
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data

# Run as non-root user for security
RUN useradd -m -s /bin/bash botuser && chown -R botuser:botuser /app/data
USER botuser

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

CMD ["python", "main.py"]
