# Deployment Guide

This guide details the procedure for deploying the unified TDS Challan & PAN Verification backend to production.

## 1. Resource Requirements
- **CPU**: Minimum 2 Cores. The simultaneous spinning of two browser engines during heavy bulk uploads can saturate a single core rapidly.
- **Memory**: Minimum **1.5 GB RAM**. 2.0 GB recommended. Playwright Chromium and Selenium Chromium run entirely distinct execution context pools.
- **Disk**: ~2GB required (1.5GB base + ~500MB runtime storage for downloads).

## 2. Docker Deployment
The official production deployment path relies on Docker. The Dockerfile encompasses all base dependencies (Selenium chromium-driver binaries from Debian, Playwright context from MS).

### Building
```bash
docker build -t tds-pan-api:v6 .
```

### Running
Ensure you mount external volumes for `data/` and `downloads/` to ensure JSON logs and Excel artifacts survive container restarts.
```bash
docker run -d \
  --name tds-pan-api \
  --restart unless-stopped \
  -p 8001:8001 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/downloads:/app/downloads \
  tds-pan-api:v6
```

## 3. Environment Variables Strategy
Use `.env` for standard deployments, but rely on native Kubernetes Secrets or Docker Swarm Secrets when orchestrating securely. Do not mount `.env` files if using a Secret Manager vault.

## 4. Rollback Procedures
If the unified API begins exhibiting memory leaks or browser crashes:
1. Re-tag the prior image build (e.g., `tds-api:v5`) and update the deployment manifest.
2. The legacy `tds-api:v5` version natively shares the identical `JOBS_FILE` persistence contract. Pending queue tasks will simply be read back into memory by the old version flawlessly.

## 5. Upgrade Procedures
Always execute upgrades with a graceful shutdown (`docker stop <container>`). The FastAPI lifespan is programmed to wait up to 5 seconds for background Selenium threads to drain and to securely terminate Playwright processes. Force killing (`docker kill`) may strand `.json` files in a half-written state!
