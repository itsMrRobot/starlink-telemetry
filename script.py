import requests
import os
import threading
import time
import re
import ipaddress
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')

BATCH_SIZE = os.getenv('BATCH_SIZE')
MAX_LINGER = os.getenv('MAX_LINGER')
METRICS_PORT = int(os.getenv('METRICS_PORT', '9100'))

required_vars = ['CLIENT_ID', 'CLIENT_SECRET', 'BATCH_SIZE', 'MAX_LINGER']
for var in required_vars:
    if not os.getenv(var):
        raise RuntimeError(f"Missing required environment variable: {var}")

# Shared buffer for latest Prometheus-formatted lines.
latest_lines = []
latest_lock = threading.Lock()
ALERT_FIELDS = ('Alerts', 'ActiveAlerts', 'ActiveAlertIds')

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return

        with latest_lock:
            body = "\n".join(latest_lines) + "\n" if latest_lines else "# no data yet\n"

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        # Silence default logging to avoid noisy stdout.
        return

def start_http_server(port):
    server = HTTPServer(("0.0.0.0", port), MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

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

def get_column_index(column_names, desired_column_name):
    """
    Gets the index for a telemtry metric (ex: ObstructionPercentTime).

    :param column_names: column names for a specific device type.
    :param desired_column_name: column you want to get index of (ex: ObstructionPercentTime).
    :return: index of the metric, or -1 if it can not be found.
    """
    index = 0
    for column_name in column_names:
        if column_name == desired_column_name:
            return index
        index += 1

    return -1

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

def sanitize_label_value(value):
    """
    Simplistic label sanitization for Prometheus-friendly output.
    """
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')

def sanitize_metric_name(name):
    """
    Convert field names to Prometheus-safe metric identifiers.
    """
    return re.sub(r'[^a-zA-Z0-9_:]', '_', name)

def to_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def ip_to_numeric(ip_str):
    """
    Convert IP string to a numeric value to avoid volatile labels.
    IPv4 -> integer form. IPv6 -> folded into a float-friendly 52-bit space.
    """
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError:
        return None

    if ip_obj.version == 4:
        return float(int(ip_obj))

    # IPv6 values exceed float precision; fold into lower 52 bits to stay stable.
    return float(int(ip_obj) & ((1 << 52) - 1))

def format_line_protocol(record, device_type_names, ip_lookup):
    """
    Convert a telemetry record into Prometheus exposition lines.
    Router records are ignored upstream; this function focuses on final formatting.
    """
    device_type_code = record.get('DeviceType')
    device_type_label = device_type_names.get(device_type_code, device_type_code)
    device_id = record.get('DeviceId')
    if device_type_code == 'i' and isinstance(device_id, str) and device_id.startswith("ip-"):
        device_id = device_id[3:]
    if record.get('UtcTimestampNs') is None or device_id is None:
        return []

    numeric_metrics = []
    info_fields = {}

    # Start with the fields present on the record itself.
    for key, value in record.items():
        if key in {'DeviceType', 'UtcTimestampNs', 'DeviceId'}:
            continue
        if key in ALERT_FIELDS:
            continue
        cleaned_value = clean_field_value(value)
        if cleaned_value is None or cleaned_value == '':
            continue

        numeric_val = to_float(cleaned_value)
        if numeric_val is not None:
            metric_name = sanitize_metric_name(f"starlink_{key}")
            numeric_metrics.append((metric_name, numeric_val))
        else:
            info_fields[sanitize_metric_name(key.lower())] = str(cleaned_value)

    base_labels = {
        'device_type': device_type_label,
        'deviceID': device_id
    }

    lines = []

    # Numeric metrics emitted individually.
    for metric_name, value in numeric_metrics:
        label_str = ",".join(f'{k}="{sanitize_label_value(v)}"' for k, v in base_labels.items())
        lines.append(f"{metric_name}{{{label_str}}} {value}")

    # IP metrics as values (not labels) to avoid volatile cardinality.
    if device_type_code != 'i':
        ip_record = ip_lookup.get(device_id)
        if ip_record:
            ip_fields = {k: ip_record.get(k) for k in ('Ipv4', 'Ipv6Ue', 'Ipv6Cpe')}
            for key, raw_val in ip_fields.items():
                if raw_val in (None, ''):
                    continue
                values = raw_val if isinstance(raw_val, list) else [raw_val]
                for idx, ip_str in enumerate(values):
                    numeric_ip = ip_to_numeric(ip_str)
                    if numeric_ip is None:
                        continue
                    label_str = ",".join(f'{k}="{sanitize_label_value(v)}"' for k, v in base_labels.items())
                    metric_suffix = f"_{idx}" if len(values) > 1 else ""
                    metric_name = sanitize_metric_name(f"starlink_{key}_numeric{metric_suffix}")
                    lines.append(f"{metric_name}{{{label_str}}} {numeric_ip}")

    # Info line with additional fields emitted as data (tags limited to device_type/deviceID).
    if info_fields:
        merged_labels = {**base_labels, **info_fields}
        label_str = ",".join(f'{k}="{sanitize_label_value(v)}"' for k, v in merged_labels.items())
        lines.append(f"starlink_info{{{label_str}}} 1")

    return lines

def format_alert_lines(record, device_type_names, alert_names_by_device):
    """
    Generate alert lines for a single record if active alerts exist.
    """
    device_type_code = record.get('DeviceType')
    if device_type_code != 'u':  # Only UserTerminal alerts are emitted.
        return []

    device_id = record.get('DeviceId')
    if device_id is None:
        return []

    alert_codes = extract_alert_codes(record)
    if not alert_codes:
        return []

    device_type_label = device_type_names.get(device_type_code, device_type_code)
    alert_name_map = alert_names_by_device.get(device_type_code, {})

    lines = []
    for code in alert_codes:
        alert_name = alert_name_map.get(code, code)
        metric_name = sanitize_metric_name(f"starlink_alert_active_{alert_name}")
        label_str = ",".join(
            [
                f'device_type="{sanitize_label_value(device_type_label)}"',
                f'deviceID="{sanitize_label_value(device_id)}"'
            ]
        )
        lines.append(f"{metric_name}{{{label_str}}} 1")
    return lines

def poll_stream():
    """
    Constantly polls telemetry API. Expect to get a response about every 15 seconds.
    When called initially, you might receive a response more often as the stream catches up.
    """
    access_token = get_starlink_access_token()
    start_http_server(METRICS_PORT)

    while (True):
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

        if (response.status_code != 200):
            # Auth token expires ~15 minutes, so refresh it if invalid response.
            access_token = get_starlink_access_token()
        else:
            response_json = response.json()

            # The raw telemetry data points for all device types.
            telemetry = response_json['data']['values']

            # If no telemetry received, don't do any processing.
            if (len(telemetry) == 0):
                continue

            column_names_by_type = response_json['data']['columnNamesByDeviceType']
            device_type_names = response_json.get('metadata', {}).get('enums', {}).get('DeviceType', {})
            alert_names_by_device = response_json.get('metadata', {}).get('enums', {}).get('AlertsByDeviceType', {})

            ip_lookup = {}
            records = []

            # Map telemetry entries to records keyed by column names, ignoring routers.
            for entry in telemetry:
                if not entry or entry[0] == 'r':
                    continue

                device_type_code = entry[0]
                columns = column_names_by_type.get(device_type_code)
                if not columns:
                    continue

                record = map_entry_to_record(entry, columns)
                records.append(record)

                if device_type_code == 'i':
                    device_id = record.get('DeviceId')
                    if device_id:
                        ip_lookup[device_id] = record

            # Emit Prometheus-friendly lines.
            collected_lines = []
            for record in records:
                lines = format_line_protocol(record, device_type_names, ip_lookup)
                for line in lines:
                    print(line, flush=True)
                collected_lines.extend(lines)

                # Emit alerts if present for user terminals.
                alert_lines = format_alert_lines(record, device_type_names, alert_names_by_device)
                for alert_line in alert_lines:
                    print(alert_line, flush=True)
                collected_lines.extend(alert_lines)

            with latest_lock:
                latest_lines[:] = collected_lines
            
            elapsed_time = time.time() - start_time
            sleep_duration = max(0, 15 - elapsed_time)
            time.sleep(sleep_duration)

if __name__ == '__main__':
    poll_stream()
