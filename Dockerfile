FROM python:3.11-slim

WORKDIR /app

# System packages needed by Playwright/Chromium
# (playwright install --with-deps handles most, but we need apt certs first)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (locked versions for reproducible builds)
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium to a shared path accessible by all users.
# Without this, `playwright install` puts browsers under ~/.cache/ms-playwright
# which differs between root (build-time) and botuser (run-time).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
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
