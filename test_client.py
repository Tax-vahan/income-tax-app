"""
Quick smoke test for the TDS Challan API.

Usage:
    # With API running locally:
    python test_client.py

    # Against a different host:
    BASE_URL=http://my-server:8001 python test_client.py
"""
import json
import os
import time
import requests

BASE_URL = os.getenv("BASE_URL", "http://localhost:8001")

PAYLOAD = {
    "tan":       "XXXX12345X",        # replace with real TAN
    "password":  "yourpassword",      # replace with real password
    "from_date": "01/01/2026",
    "to_date":   "31/03/2026",
}


def main() -> None:
    print(f"API base: {BASE_URL}\n")

    # 1. Health check
    r = requests.get(f"{BASE_URL}/tds/api/health", timeout=10)
    print("Health:", r.json())

    # 2. Submit a fetch job
    r = requests.post(f"{BASE_URL}/tds/api/v1/fetch", json=PAYLOAD, timeout=15)
    r.raise_for_status()
    job = r.json()
    job_id = job["job_id"]
    print(f"Job created: {job_id}  status={job['status']}")

    # 3. Poll until done (max 5 min)
    for _ in range(60):
        time.sleep(5)
        r = requests.get(f"{BASE_URL}/tds/api/v1/jobs/{job_id}", timeout=10)
        job = r.json()
        print(f"  status={job['status']}")
        if job["status"] in ("completed", "failed"):
            break

    print("\nFinal job state:")
    print(json.dumps(job, indent=2))

    if job["status"] == "completed":
        print(f"\nDownload: {BASE_URL}/tds/api/v1/jobs/{job_id}/download")


if __name__ == "__main__":
    main()
