import os
import time
import requests
import logging
import sys
import h3
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import datetime

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
ACCOUNT_NUMBER = os.getenv('ACCOUNT_NUMBER')

INFLUXDB_URL = os.getenv('INFLUXDB_URL')
INFLUXDB_TOKEN = os.getenv('INFLUXDB_TOKEN')
INFLUXDB_ORG = os.getenv('INFLUXDB_ORG')
INFLUXDB_BUCKET = os.getenv('INFLUXDB_BUCKET')
INFLUXDB_BUCKET_2 = os.getenv('INFLUXDB_BUCKET_2')

def get_starlink_access_token():
    response = requests.post(
        'https://api.starlink.com/auth/connect/token',
        data={
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'client_credentials'
        }
    )
    response.raise_for_status()
    return response.json()['access_token']

def poll_starlink_telemetry(access_token):
    response = requests.post(
        'https://web-api.starlink.com/telemetry/stream/v1/telemetry',
        json={
            "accountNumber": ACCOUNT_NUMBER,
            "batchSize": 1000,
            "maxLingerMs": 15000
        },
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
    )
    return response

import h3

def write_telemetry_to_influx(values, column_names, write_api):
    for entry in values:
        device_type = entry[0]
        timestamp_ns = entry[1]
        device_id = entry[2]

        fields = {}
        tags = {'device_id': device_id}
        
        lat = long = None

        for idx, value in enumerate(entry[3:], start=3):
            col_name = column_names[device_type][idx]

            # Handle H3CellId conversion directly
            if col_name == "H3CellId" and value:
                try:
                    h3_cell_id_int = int(value)
                    h3_hex = hex(h3_cell_id_int)
                    if h3.is_valid_cell(h3_hex):
                        lat, long = h3.cell_to_latlng(h3_hex)
                except (ValueError, h3.H3CellError):
                    logging.warning(f"Invalid H3 cell ID encountered: {value}")
                continue  # Skip writing H3CellId directly to influx

            if isinstance(value, list):
                fields[col_name] = ','.join(str(v) for v in value)
            elif isinstance(value, (int, float)):
                fields[col_name] = float(value)
            elif isinstance(value, str) and value.strip() == "":
                continue
            else:
                try:
                    fields[col_name] = float(value)
                except (ValueError, TypeError):
                    continue

        # Include lat/long if successfully extracted
        if lat is not None and long is not None:
            fields['latitude'] = lat
            fields['longitude'] = long

        if not fields:
            continue

        point = Point(f'starlink_{device_type}') \
            .time(datetime.datetime.now(datetime.UTC), WritePrecision.S)

        # Add fields
        for key, val in fields.items():
            point.field(key, val)
        
        # Add tags
        for tag_key, tag_val in tags.items():
            point.tag(tag_key, tag_val)

        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)

def write_alerts_to_influx(values_alerts, write_api):
    for device_type, alerts in values_alerts.items():
        for code, name in alerts.items():
            point = (
                Point("device_alert")
                .tag("device_type", device_type)
                .tag("code", code)
                .field("alert_name", name)
                .time(datetime.datetime.utcnow(), WritePrecision.S)
            )
            write_api.write(bucket=INFLUXDB_BUCKET_2, org=INFLUXDB_ORG, record=point)
        logging.info(f"Wrote {len(alerts)} '{device_type}' alert types to InfluxDB.")

def main():
    access_token = get_starlink_access_token()
    influx_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    try:
        while True:
            start_time = time.time()
            response = poll_starlink_telemetry(access_token)

            if response.status_code == 401:
                logging.info("Access token expired, refreshing...")
                access_token = get_starlink_access_token()
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError as e:
                logging.error(f"HTTP error: {e}")
                continue

            data = response.json().get('data', {})
            values = data.get('values', [])
            column_names = data.get('columnNamesByDeviceType', {})
            alert_names = data.get('AlertsByDeviceType', {})

            if values:
                write_telemetry_to_influx(values, column_names, write_api)
                logging.info(f"Wrote {len(values)} telemetry points to InfluxDB.")
                write_alerts_to_influx(alert_names, write_api)
            else:
                logging.info("No new telemetry data.")

            elapsed_time = time.time() - start_time
            sleep_duration = max(0, 15 - elapsed_time)
            time.sleep(sleep_duration)
    except KeyboardInterrupt:
        logging.info("Terminated by user.")
    finally:
        influx_client.close()

if __name__ == '__main__':
    main()
