import json
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
BATCH_SIZE = int(os.getenv('BATCH_SIZE', '1000'))
MAX_LINGER = int(os.getenv('MAX_LINGER', '15000'))

CLICKHOUSE_URL = os.getenv('CLICKHOUSE_URL')
CLICKHOUSE_USER = os.getenv('CLICKHOUSE_USER', 'default')
CLICKHOUSE_PASSWORD = os.getenv('CLICKHOUSE_PASSWORD', '')
CLICKHOUSE_DB = os.getenv('CLICKHOUSE_DB', 'default')

required_vars = ['CLIENT_ID', 'CLIENT_SECRET', 'CLICKHOUSE_URL']
for var in required_vars:
    if not os.getenv(var):
        raise RuntimeError(f"Missing required environment variable: {var}")

ALERT_FIELDS = ('Alerts', 'ActiveAlerts', 'ActiveAlertIds')

def get_starlink_access_token():
    return requests.post(
        'https://api.starlink.com/auth/connect/token',
        data=
            {
                'client_id' : CLIENT_ID,
                'client_secret' : CLIENT_SECRET,
                'grant_type' : 'client_credentials'
            }
    ).json()['access_token']

def map_entry_to_record(entry, columns):
    """
    Build a record dict by zipping telemetry entry values with their column names.
    """
    return {column: entry[idx] for idx, column in enumerate(columns) if idx < len(entry)}

def clean_field_value(value):
    """
    Normalize values for output: collapse lists and skip empty values.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return ';'.join(str(v) for v in value if v is not None)
    return value

def extract_alert_codes(record):
    """
    Return a list of alert codes from a telemetry record, if present.
    """
    for field in ALERT_FIELDS:
        if field in record:
            val = record[field]
            if isinstance(val, list):
                return [str(v) for v in val if v is not None]
            if val not in (None, ''):
                return [str(val)]
    return []

def to_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def normalize_device_id(device_type_code, device_id):
    if device_type_code == 'i' and isinstance(device_id, str) and device_id.startswith("ip-"):
        return device_id[3:]
    return device_id

def ensure_tables():
    ddl_statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {CLICKHOUSE_DB}.telemetry
        (
            device_type String,
            device_id String,
            ts_ns UInt64,
            metrics Map(String, Float64),
            info Map(String, String)
        )
        ENGINE = MergeTree
        ORDER BY (device_id, ts_ns)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {CLICKHOUSE_DB}.alerts
        (
            device_type String,
            device_id String,
            ts_ns UInt64,
            alert_name String
        )
        ENGINE = MergeTree
        ORDER BY (device_id, ts_ns)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {CLICKHOUSE_DB}.ip_allocations
        (
            device_id String,
            ts_ns UInt64,
            ipv4 Array(String),
            ipv6_ue Array(String),
            ipv6_cpe Array(String)
        )
        ENGINE = MergeTree
        ORDER BY (device_id, ts_ns)
        """
    ]
    for ddl in ddl_statements:
        execute_clickhouse_query(ddl)

def execute_clickhouse_query(query):
    backoff = 1
    while True:
        try:
            resp = requests.post(
                CLICKHOUSE_URL,
                params={'query': query},
                auth=(CLICKHOUSE_USER, CLICKHOUSE_PASSWORD),
                timeout=20,
            )
            if resp.status_code == 200:
                return
            else:
                print(f"ClickHouse query failed ({resp.status_code}): {resp.text}", flush=True)
        except requests.RequestException as exc:
            print(f"ClickHouse request error: {exc}", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)

def insert_json_rows(table, rows):
    if not rows:
        return
    query = f"INSERT INTO {CLICKHOUSE_DB}.{table} FORMAT JSONEachRow"
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    backoff = 1
    while True:
        try:
            resp = requests.post(
                CLICKHOUSE_URL,
                params={'query': query},
                data=payload.encode('utf-8'),
                auth=(CLICKHOUSE_USER, CLICKHOUSE_PASSWORD),
                timeout=20,
            )
            if resp.status_code == 200:
                return
            else:
                print(f"Insert to {table} failed ({resp.status_code}): {resp.text}", flush=True)
        except requests.RequestException as exc:
            print(f"Insert to {table} request error: {exc}", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)

def build_rows(telemetry, column_names_by_type, device_type_names, alert_names_by_device):
    telemetry_rows = []
    alert_rows = []
    ip_rows = []

    for entry in telemetry:
        if not entry or entry[0] == 'r':
            continue

        device_type_code = entry[0]
        columns = column_names_by_type.get(device_type_code)
        if not columns:
            continue

        record = map_entry_to_record(entry, columns)
        device_id_raw = record.get('DeviceId')
        device_id = normalize_device_id(device_type_code, device_id_raw)
        ts_ns = record.get('UtcTimestampNs')
        if device_id is None or ts_ns is None:
            continue

        if device_type_code == 'i':
            ipv4 = record.get('Ipv4') if isinstance(record.get('Ipv4'), list) else ([] if record.get('Ipv4') is None else [record.get('Ipv4')])
            ipv6_ue = record.get('Ipv6Ue') if isinstance(record.get('Ipv6Ue'), list) else ([] if record.get('Ipv6Ue') is None else [record.get('Ipv6Ue')])
            ipv6_cpe = record.get('Ipv6Cpe') if isinstance(record.get('Ipv6Cpe'), list) else ([] if record.get('Ipv6Cpe') is None else [record.get('Ipv6Cpe')])
            ip_rows.append(
                {
                    "device_id": device_id,
                    "ts_ns": int(ts_ns),
                    "ipv4": [str(v) for v in ipv4],
                    "ipv6_ue": [str(v) for v in ipv6_ue],
                    "ipv6_cpe": [str(v) for v in ipv6_cpe],
                }
            )
            continue

        metrics = {}
        info = {}
        for key, value in record.items():
            if key in {'DeviceType', 'UtcTimestampNs', 'DeviceId'} or key in ALERT_FIELDS:
                continue
            cleaned_value = clean_field_value(value)
            if cleaned_value is None or cleaned_value == '':
                continue
            numeric_val = to_float(cleaned_value)
            if numeric_val is not None:
                metrics[key] = numeric_val
            else:
                info[key] = str(cleaned_value)

        telemetry_rows.append(
            {
                "device_type": device_type_names.get(device_type_code, device_type_code),
                "device_id": device_id,
                "ts_ns": int(ts_ns),
                "metrics": metrics,
                "info": info,
            }
        )

        if device_type_code == 'u':
            alert_codes = extract_alert_codes(record)
            if alert_codes:
                alert_name_map = alert_names_by_device.get(device_type_code, {})
                for code in alert_codes:
                    alert_name = alert_name_map.get(code, code)
                    alert_rows.append(
                        {
                            "device_type": device_type_names.get(device_type_code, device_type_code),
                            "device_id": device_id,
                            "ts_ns": int(ts_ns),
                            "alert_name": alert_name,
                        }
                    )

    return telemetry_rows, alert_rows, ip_rows

def poll_stream():
    """
    Constantly polls telemetry API. Expect to get a response about every 15 seconds.
    When called initially, you might receive a response more often as the stream catches up.
    """
    access_token = get_starlink_access_token()
    ensure_tables()

    while True:
        start_time = time.time()
        response = requests.post(
            'https://starlink.com/api/public/v2/telemetry/stream',
            json=
                {
                    "batchSize": BATCH_SIZE,
                    "maxLingerMs": MAX_LINGER
                },
            headers=
                {
                    'content-type' : 'application/json',
                    'accept' : '*/*',
                    'Authorization' : 'Bearer '+access_token
                }
        )

        if response.status_code != 200:
            # Auth token expires ~15 minutes, so refresh it if invalid response.
            access_token = get_starlink_access_token()
            continue

        response_json = response.json()

        data_section = response_json.get('data', {})
        telemetry = data_section.get('values', [])

        if len(telemetry) == 0:
            continue

        column_names_by_type = data_section.get('columnNamesByDeviceType', {})
        device_type_names = response_json.get('metadata', {}).get('enums', {}).get('DeviceType', {})
        alert_names_by_device = response_json.get('metadata', {}).get('enums', {}).get('AlertsByDeviceType', {})

        telemetry_rows, alert_rows, ip_rows = build_rows(
            telemetry,
            column_names_by_type,
            device_type_names,
            alert_names_by_device
        )

        insert_json_rows('telemetry', telemetry_rows)
        insert_json_rows('alerts', alert_rows)
        insert_json_rows('ip_allocations', ip_rows)

        elapsed_time = time.time() - start_time
        sleep_duration = max(0, 15 - elapsed_time)
        time.sleep(sleep_duration)

if __name__ == '__main__':
    poll_stream()
