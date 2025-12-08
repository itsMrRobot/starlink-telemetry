Starlink Telemetry → ClickHouse
===============================

Python service that authenticates with the Starlink Enterprise API, streams telemetry for an account, and writes telemetry, alerts, and IP allocation data directly into ClickHouse.

How it works
------------
- Obtain an OAuth token from `https://starlink.com/api/public/auth/connect/token` using client credentials.
- Stream telemetry via `v2/telemetry/stream` using `BATCH_SIZE` and `MAX_LINGER`.
- Map column metadata per device type, merge IP allocation rows into UserTerminal rows, and build batch inserts:
  - Telemetry rows: numeric fields → `metrics` Map(String,Float64); non-numeric fields → `info` Map(String,String); stored with `device_type`, `device_id`, `ts_ns`.
  - Alert rows: resolved alert names (codes mapped via `metadata.enums.AlertsByDeviceType`) with `device_type`, `device_id`, `ts_ns`.
  - IP allocation rows: arrays of IPv4/IPv6 strings per device_id with `ts_ns`.
- Data is written to ClickHouse over HTTP using `JSONEachRow` with retry/backoff; polling halts on write failure to avoid losing cached telemetry.

Environment variables
---------------------
All required unless noted.

| Name | Description | Example |
| --- | --- | --- |
| `CLIENT_ID` | Starlink Enterprise API client id | `abc123` |
| `CLIENT_SECRET` | Starlink Enterprise API client secret | `shh-very-secret` |
| `BATCH_SIZE` | Number of telemetry records per poll | `1000` |
| `MAX_LINGER` | Max wait in ms before API responds | `15000` |
| `CLICKHOUSE_URL` | ClickHouse HTTP endpoint | `http://clickhouse:8123` |
| `CLICKHOUSE_USER` | ClickHouse user | `starlink` |
| `CLICKHOUSE_PASSWORD` | ClickHouse password | `change_me` |
| `CLICKHOUSE_DB` | Target database | `starlink` |

Example `.env`
--------------
```
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
BATCH_SIZE=1000
MAX_LINGER=15000

CLICKHOUSE_URL=http://clickhouse:8123
CLICKHOUSE_USER=starlink
CLICKHOUSE_PASSWORD=change_me
CLICKHOUSE_DB=starlink
```

Run with Docker Compose
-----------------------
1. Create `.env` in the repo root with the variables above.
2. Start the services:
   ```
   docker compose up -d
   ```
3. Check logs:
   ```
   docker compose logs -f starlink-telemetry
   ```

Build and run locally with Docker
---------------------------------
```
docker build -t starlink-telemetry -f Docker/Dockerfile .
docker run --env-file .env --network your-network --name starlink-telemetry --restart always starlink-telemetry
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
- Inserts use retry/backoff and block further polling on failure to avoid data loss.
- For additional details on the Starlink Enterprise API, see: https://starlink.readme.io/docs/getting-started
