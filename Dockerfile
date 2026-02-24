FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create data directory for SQLite DB and logs
RUN mkdir -p /app/data

# Run the bot
CMD ["python", "main.py"]
