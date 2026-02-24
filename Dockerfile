FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Persistent data directory (DB + logs).  Override with DATA_DIR env var.
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data

CMD ["python", "main.py"]
