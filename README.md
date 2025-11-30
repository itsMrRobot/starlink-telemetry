Starlink Telemetry Prometheus Exporter
======================================

Python service that authenticates with the Starlink Enterprise API, streams telemetry for an account, and exposes Prometheus metrics on `/metrics`. Telemetry values and active alerts are rendered as Prometheus exposition text for scraping.

How it works
------------
- Obtain an OAuth token from `https://starlink.com/api/public/auth/connect/token` using client credentials.
- Stream telemetry via `v2/telemetry/stream` using `BATCH_SIZE` and `MAX_LINGER`.
- Map column metadata per device type, merge IP allocation rows into UserTerminal rows, and emit Prometheus metrics:
  - Numeric alert fields become `starlink_<field>` gauges with `device_type` and `deviceID` labels.
  - String/list fields become a single `starlink_info{...} 1` label set per device.
  - Active alerts emit `starlink_alert_active{device_type,deviceID,alert} 1`; the alert name is resolved from `metadata.enums.AlertsByDeviceType` each poll.
- Metrics are served from an in-process HTTP server on `0.0.0.0:<METRICS_PORT>` (default `9100`).

Environment variables
---------------------
All required unless noted.

| Name | Description | Example |
| --- | --- | --- |
| `CLIENT_ID` | Starlink Enterprise API client id | `abc123` |
| `CLIENT_SECRET` | Starlink Enterprise API client secret | `shh-very-secret` |
| `BATCH_SIZE` | Number of telemetry records per poll | `1000` |
| `MAX_LINGER` | Max wait in ms before API responds | `15000` |
| `METRICS_PORT` | (Optional) Port for `/metrics` | `9100` |

Example `.env`
--------------
```
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
BATCH_SIZE=1000
MAX_LINGER=15000
# METRICS_PORT=9100
```

Prometheus scraping
-------------------
- Endpoint: `http://<host>:9100/metrics` (or your `METRICS_PORT`).
- Suggested `scrape_interval`: 20–30s with `MAX_LINGER=15000` (1.5–2× the expected batch cadence).
- Only active alerts produce `starlink_alert_active` lines; router records are dropped.

Run with Docker Compose
-----------------------
1. Create `.env` in the repo root with the variables above.
2. (Optional) expose the metrics port if scraping from outside the container:
   ```yaml
   services:
     starlink-telemetry:
       ports:
         - "9100:9100"
   ```
3. Start the service:
   ```
   docker compose up -d
   ```
4. Check logs:
   ```
   docker compose logs -f starlink-telemetry
   ```

Build and run locally with Docker
---------------------------------
```
docker build -t starlink-telemetry -f Docker/Dockerfile .
docker run --env-file .env -p 9100:9100 --name starlink-telemetry --restart always starlink-telemetry
```

Run without Docker
------------------
```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python script.py
```

Notes
-----
- The script refreshes the token automatically on non-200 responses.
- Timestamps are converted from ns to ms for Prometheus.
- For additional details on the Starlink Enterprise API, see: https://starlink.readme.io/docs/getting-started
