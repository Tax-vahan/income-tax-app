# ════════════════════════════════════════════════════════════════════
#  TDS Challan Fetcher — Production Dockerfile
#
#  Uses Chromium from Debian repos (no apt-key / GPG gymnastics).
#  Chromium + chromium-driver are always version-matched by Debian.
#
#  Build:   docker build -t tds-api .
#  Run:     docker compose up -d
# ════════════════════════════════════════════════════════════════════

FROM python:3.11-slim

# ── System dependencies ──────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        # Headless Chromium + matching driver (always version-paired by Debian)
        chromium \
        chromium-driver \
        # Runtime libs Chromium needs
        fonts-liberation \
        libnss3 \
        libxss1 \
        libglib2.0-0 \
        # Networking utilities
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python runtime config ────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data \
    # Tell Selenium / auth.py where the binaries live
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_BIN=/usr/bin/chromedriver

# ── Application ──────────────────────────────────────────────────────
WORKDIR /app

# Install Python deps first (layer-cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the service needs (see .dockerignore for exclusions)
COPY api_service.py .
COPY fetcher/      ./fetcher/

# Pre-create runtime directories so volume mounts work cleanly
RUN mkdir -p downloads data


# ── Networking ───────────────────────────────────────────────────────
EXPOSE 8001

# ── Health check ─────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8001/tds/api/health || exit 1

# ── Entrypoint ───────────────────────────────────────────────────────
CMD ["uvicorn", "api_service:app", \
     "--host", "0.0.0.0", \
     "--port", "8001", \
     "--workers", "1", \
     "--log-level", "info"]
