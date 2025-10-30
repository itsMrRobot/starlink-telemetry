You need a .env file with the following information set:
```
CLIENT_ID=xxxx from starlink 
CLIENT_SECRET=starlink client secret
ACCOUNT_NUMBER=starlink account number

INFLUXDB_URL=http://influxdb:8086
INFLUXDB_TOKEN=influx token
INFLUXDB_ORG=influx org
INFLUXDB_BUCKET=starlink
INFLUXDB_BUCKET_2=alert_type

BATCH_SIZE=size of batch to poll from starlink, default is 1000
MAX_LINGER=frequency of polling starlink api, default is 15000 (15 seconds)
```
