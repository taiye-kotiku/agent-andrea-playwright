# Use official Microsoft Playwright image - has Chromium pre-installed
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Start server
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}