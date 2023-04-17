# Webmon
Webmon is a website availability monitoring tool, populating a database, via a Kafka.

## Description

*Webmon* is a website availability monitoring tool. It pushes results to an PostgreSQL database. It also uses a Kafa topic in the middle to decouple to collection of the data and the storage of the metrics.

It tries to access the listed list of websites on a regular basis. It matches the content of the responses against a regular expression dedicate to each website.
Then it stores data in two steps:
1. it pushes events to a Kafka topic, as a serialized JSON

```
b'{"rsp_tm": 42000, "status": 200, "url": "https://google.com", "check": true, 
"timestamp": "2023-04-16 09:02:42.068288+00:00", "source": "192.168.1.6"}'
```

2. it retrieves the JSON from Kafka and inserts a row in a PostgreSQL table

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

The first time you may need to generate and **edit** the configuration file:

```shell
python3 -m webmon --generate-config config.yaml
```

Then, you can monitor websites by listing them in the config file and/or in the command line.

- in config.yaml:

```yaml
websites:
    - url: "https://www.google.com"
      regex: "<title>Google</title>"
      period: 120
    - url: "https://google.com"
      regex: "<title>Google</title>"
      period: 10
```

- in the command line:

```shell
python3 -m webmon --config config.yaml \
        --website-regex-period "https://www.google.com" "<title>Google</title>" 120 \
        --website-regex-period "https://google.com" "<title>Google</title>" 120
```

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

It can be extended in many ways:
- make use of Schemas to generically decode/encode/transform from one data structure to another, eg. **Kafka Connect**
- implement generic metric factories to allow to populate not only one database table, eg. **Apache Flink**
- make use of **systemd** to manage the underlying services
- propose to generate graphs in an Observability Platform, eg. **Grafana**