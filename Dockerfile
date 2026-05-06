FROM python:3.11-slim

WORKDIR /app

# Install system deps for Playwright
RUN apt-get update && apt-get install -y \
    curl \
    libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 \
    libdrm2 libdbus-1-3 libexpat1 libxcb1 libxkbcommon0 \
    libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64 \
    libatspi2.0-0t64 libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

# Install Lightpanda browser
RUN curl -L -o /usr/local/bin/lightpanda \
    https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux && \
    chmod +x /usr/local/bin/lightpanda

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080 9222

# Start Lightpanda CDP server, then run the app
CMD ["sh", "-c", "lightpanda serve --host 0.0.0.0 --port 9222 --obey-robots=false & sleep 2 && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
