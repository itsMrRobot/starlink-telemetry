Starlink Telemetry Collector
============================

Python service that authenticates with the Starlink Enterprise API, streams telemetry for a single account, and writes both telemetry fields and alert metadata to InfluxDB. The script converts H3 cell IDs into latitude/longitude before writing so location is immediately usable downstream.

How it works
------------
- Obtain an OAuth token from `https://starlink.com/api/public/auth/connect/token` using your client credentials.
- Stream telemetry via `v2/telemetry/stream` for the configured `ACCOUNT_NUMBER` with the requested batch size and linger settings.
- Parse values/column metadata per device type, convert H3 cells to `latitude`/`longitude`, and write measurements to InfluxDB (`starlink_<device_type>`).
- Persist alert codes/names as separate `device_alert` points in a dedicated bucket.

Environment variables
---------------------
All variables are required; the script exits if any are missing.

| Name | Description | Example |
| --- | --- | --- |
| `CLIENT_ID` | Starlink Enterprise API client id | `abc123` |
| `CLIENT_SECRET` | Starlink Enterprise API client secret | `shh-very-secret` |
| `ACCOUNT_NUMBER` | Target Starlink account number | `0000123456` |
| `INFLUXDB_URL` | InfluxDB base URL | `http://influxdb:8086` |
| `INFLUXDB_TOKEN` | InfluxDB API token with write access | `my-influx-token` |
| `INFLUXDB_ORG` | InfluxDB org name | `my-org` |
| `INFLUXDB_BUCKET` | Bucket for telemetry points | `starlink` |
| `INFLUXDB_BUCKET_2` | Bucket for alert metadata | `alert_type` |
| `BATCH_SIZE` | Number of telemetry records per poll (e.g., `1000`) | `1000` |
| `MAX_LINGER` | Max wait in ms before API responds (e.g., `15000`) | `15000` |

Example `.env`
--------------
```
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
ACCOUNT_NUMBER=your-account-number

INFLUXDB_URL=http://influxdb:8086
INFLUXDB_TOKEN=your-influx-token
INFLUXDB_ORG=your-org
INFLUXDB_BUCKET=starlink
INFLUXDB_BUCKET_2=alert_type

BATCH_SIZE=1000
MAX_LINGER=15000
```

Run with Docker Compose
-----------------------
1. Create `.env` in the repo root with the variables above.
2. Start the service:
   ```
   docker compose up -d
   ```
   The included `docker-compose.yml` uses the published image `itsmrrobot/starlink-telemetry:latest` and mounts your `.env`.
3. Check logs:
   ```
   docker compose logs -f starlink-telemetry
   ```

Build and run locally with Docker
---------------------------------
If you prefer to build locally instead of using the published image:
```
docker build -t starlink-telemetry -f Docker/Dockerfile .
docker run --env-file .env --name starlink-telemetry --restart always starlink-telemetry
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
- The script refreshes the token automatically on HTTP 401 responses.
- Influx points are written with current UTC time; adjust inside `script.py` if you need to use API timestamps instead.
- For additional details on the Starlink Enterprise API, see the docs: https://starlink.readme.io/docs/getting-started
