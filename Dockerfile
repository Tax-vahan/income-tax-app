# ════════════════════════════════════════════════════════════════════
#  TDS Challan Fetcher — Production Dockerfile
#
#  Build:   docker build -t tds-api .
#  Run:     docker compose up -d
# ════════════════════════════════════════════════════════════════════

# ── Stage 1: base layer with system deps ────────────────────────────
FROM python:3.11-slim AS base

# Avoid interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive

# Install Google Chrome stable + matching ChromeDriver in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        gnupg \
        curl \
        unzip \
        ca-certificates \
        # Fonts for headless rendering
        fonts-liberation \
        libatk-bridge2.0-0 \
        libgtk-3-0 \
        libnss3 \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        xdg-utils \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Install ChromeDriver that matches the installed Chrome version
RUN CHROME_VERSION=$(google-chrome --version | grep -oP '\d+\.\d+\.\d+') \
    && DRIVER_VERSION=$(curl -s "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_${CHROME_VERSION%%.*}") \
    && wget -q "https://chromedriver.storage.googleapis.com/${DRIVER_VERSION}/chromedriver_linux64.zip" \
    && unzip chromedriver_linux64.zip -d /usr/local/bin/ \
    && rm chromedriver_linux64.zip \
    && chmod +x /usr/local/bin/chromedriver

# ── Stage 2: application ─────────────────────────────────────────────
FROM base AS app

# Runtime environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHROME_BIN=/usr/bin/google-chrome-stable \
    CHROMEDRIVER_BIN=/usr/local/bin/chromedriver

WORKDIR /app

# Install Python deps first (layer-cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the application needs
COPY api_service.py .
COPY fetcher/ ./fetcher/

# Pre-create runtime directories
RUN mkdir -p downloads

# Expose FastAPI port
EXPOSE 8001

# Health check — hits the lightweight /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8001/tds/api/health || exit 1

# Run with uvicorn (production-grade ASGI server)
CMD ["uvicorn", "api_service:app", "--host", "0.0.0.0", "--port", "8001", \
     "--workers", "1", "--log-level", "info"]
