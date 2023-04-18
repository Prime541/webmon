# Webmon
Webmon is a website availability monitoring tool, populating a database, via a Kafka.

## Description

*Webmon* is a website availability monitoring tool. It pushes results to a PostgreSQL database. It also uses a Kafa topic in the middle to decouple to collection of data and the storage in database.

It sends HTTP requests to a list of websites on a regular basis. It matches the content of the responses against a regular expression dedicated to each website.
Then it saves the data in two steps:
1. it pushes metrics to a Kafka topic, in a serialized JSON

```
b'{"rsp_tm": 42000, "status": 200, "url": "https://google.com", "check": true, 
"timestamp": "2023-04-16 09:02:42.068288+00:00", "source": "192.168.1.6"}'
```

2. it retrieves the JSON from Kafka and inserts it in a PostgreSQL table as a row

```
INSERT INTO default_table (time_stamp, source, target, elasped_us, status, valid)
VALUES (%s, %s, %s, %s, %s, %s)
('2023-04-16 09:02:42.068288+00:00', '192.168.1.6', 'https://google.com', 42000, 200, True)
```

## Install (short version)

Download or clone this repository locally.

Build and install the module locally:

```shell
python3 -m build
python3 -m pip install .
```

Run webmon:

```shell
python3 -m webmon
```

## Install (with testing)

Download or clone this repository locally.

You may want to install the required python modules, possibly in a **venv**:

```shell
python3 -m venv myvenv
. myvenv/Scripts/activate
# Later...
deactivate
```

Build, install and test the **webmon** module locally:

```shell
python3 -m build

python3 -m pip install -e .[test]

python3 -m pytest --cov --cov-report=html:htmlcov -vv
python3 -m bandit -r src tests
python3 -m pylint --disable=C0301 src tests
python3 -m flake8 --ignore=E501 src tests
```

You can display the results of the code coverage in your web browser:

`file:///path/to/repository/htmlcov/index.html`

Run webmon:

```shell
python3 -m webmon
```

## Configuration

The first time you may need to generate and **edit** a configuration file:

```shell
python3 -m webmon --generate-config config.yaml
```

Here is the generated *config.yaml* file:

```yaml
# See: https://kafka-python.readthedocs.io/en/master/apidoc/KafkaProducer.html
kafka-producer:
    bootstrap_servers: "kafka-ip:9092"
    security_protocol: "SSL"
    ssl_cafile: "/path/to/ca.pem"
    ssl_certfile: "/path/to/service.cert"
    ssl_keyfile: "/path/to/service.key"
    # Settings for batches of messages:
    #batch_size: 16384
    #linger_ms: 0
    #compression_type:

# See: https://kafka-python.readthedocs.io/en/master/apidoc/KafkaConsumer.html
kafka-consumer:
    bootstrap_servers: "kafka-ip:9092"
    security_protocol: "SSL"
    ssl_cafile: "/path/to/ca.pem"
    ssl_certfile: "/path/to/service.cert"
    ssl_keyfile: "/path/to/service.key"
    client_id: "CONSUMER_CLIENT_ID"
    group_id: "CONSUMER_GROUP_ID"

# See: https://www.psycopg.org/docs/module.html
pg-connect:
    dsn: "postgres://user:password@host-ip:5432/db-name?sslmode=require"

kafka-topic: "default_topic"

pg-table:  "default_table"

#websites:
#    - url: "https://www.google.com"
#      regex: "<title>Google</title>"
#      period: 120
#    - url: "https://google.com"
#      regex: "<title>Google</title>"
#      period: 10
```

After you have **edited** your settings, you can monitor some websites by listing them in the config file and/or in the command line.

- in the config.yaml:

```yaml
websites:
    - url: "https://www.google.com"
      regex: "<title>Google</title>"
      period: 120
    - url: "https://google.com"
      regex: "<title>Bing</title>"
      period: 10
```

- in the command line:

```shell
python3 -m webmon --config config.yaml \
    --website-regex-period "https://www.google.com" "<title>Google</title>" 42 \
    --website-regex-period "https://google.com" "<title>Google</title>" 10
```

The websites in the command line overrides the websites from the configuration if they have the same 'url'.

Please consult the help content:

```shell
python3 -m webmon --help
```

## Uninstall

```shell
python3 -m pip uninstall --yes webmon
```

## Extensions

This is a *work in progress*.

It can be extended in several ways:
- use Schemas to generically decode/encode/transform data from one structure to another, eg. **Apache Flink**, **Kafka Connect**
- propose more providers on all ends, eg. **HTTP POST**, **Couchbase**, **M3DB**
- use **systemd** to manage the services of *webmon*
- propose to generate graphs in an Observability Platform, eg. **Grafana**
