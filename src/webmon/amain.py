""" This module is the main entry point to start the services that check the availability
    of website and populates a PostgreSQL database with some metrics, all that through an
    instance of Kafka in-between.

    It is running two services, WebPingerSvc and PostgreSQLInserterSvc, using co-routines.
    The services could be easilly started separately in different threads, processes or hosts,
    thanks to their separate code. They could also be started with multiple recplicas to work in parallel.

    A configuration file is needed. See the -c | --conf config.yaml option.
    The website to be monitored can be listed in the config file and overriden with the -w option.

    SECURITY: the config file should not be readable by everyone, since it contains secret.

    The process is running in foreground.
    Though, it can be stopped by pressing Ctrl + C or by sending a SIGINT (2) signal to it.
    If needed, it could be easily daemonized with some popular frameworks.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys

import yaml

from webmon.service import ServiceGroup
from webmon.web_pinger import WebPingerSvc
from webmon.db_inserter import PostgreSQLInserterSvc


DUMMY_CONFIG_YAML = """# See: https://kafka-python.readthedocs.io/en/master/apidoc/KafkaProducer.html
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
"""


def parse_args(argv):
    """ Parse the program arguments.
        'argv' can be set to sys.argv, starting with the program name. """
    parser = argparse.ArgumentParser(
        prog='python3 -m webmon',
        description='Check the availability of websites and populate a PostgreSQL database with some metrics, all that through a Kafka topic.',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""Example:
        python3 -m webmon --generate-conf config.yaml
        # Edit config.yaml with your settings...
        python3 -m webmon -w "https://www.google.com" "<title>Google</title>"    120 \\
                          -w "https://google.com"     "<title>[a-zA-Z]+</title>"  10 \\
                          -w "https://bing.com"       "<title>Bing</title>"       60""")
    parser.add_argument('-v', '--verbose', default=0, action='count', help='increase output verbosity')
    parser.add_argument('-c', '--config', default='config.yaml', metavar='IFILE=config.yaml', help='configuration file. It should not be readable by everybody')
    parser.add_argument('-g', '--generate-config', type=str, metavar='OFILE', help='generate a dummy configuration file')
    parser.add_argument('-w', '--website-regex-period', nargs=3, default=[], metavar=('URL', 'REGEX', 'SECONDS'), action='append', help='the URL whose content is to be checked against a REGEX on a regular period of SECONDS')
    args = parser.parse_args(argv[1:])

    if args.verbose > 1:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose > 0:
        logging.getLogger().setLevel(logging.INFO)
    else:
        # The default level of the root logger is already WARNING.
        # See: https://docs.python.org/3/library/logging.html#logging.Logger.setLevel
        logging.getLogger().setLevel(logging.WARNING)

    logging.debug('args = %s', args)

    if args.generate_config:
        logging.debug('Generating config file: %s', args.generate_config)
        with open(args.generate_config, 'w', encoding='utf-8') as config_file:
            config_file.write(DUMMY_CONFIG_YAML)
            print('Do not forget to edit the configuration file:', args.generate_config)
        return args, None

    config = {}
    try:
        with open(args.config, 'r', encoding='utf-8') as config_file:
            # The load() function is deprecated because it allows arbitrary code execution.
            # See: https://python.land/data-processing/python-yaml#PyYAML_safe_load_vs_load
            config = yaml.safe_load(config_file)
    except (FileNotFoundError, yaml.scanner.ScannerError) as exp:
        logging.error(exp)

    if not config:
        # The config file does not exist or is corrupted.
        print('The configuration file could not be loaded:', args.config)
        print('You can run this command to generate it:')
        print('   python3 -m webmon --generate-config', args.config)
        return args, None

    if 'websites' not in config:
        config['websites'] = []
    for website, regex, period in args.website_regex_period:
        config_element = {
            'url': website,
            'regex': regex,
            'period': int(period)}
        found = False
        for i in range(len(config['websites'])):
            if config['websites'][i]['url'] == website:
                config['websites'][i] = config_element
                found = True
                break
        if not found:
            config['websites'].append(config_element)
    print('Number of websites to monitor:', len(config['websites']))

    logging.debug('config = %s', config)
    return args, config


async def amain():
    """ Main asynchronous entry point.
        It should be run with asyncio.run(main()) """
    _, config = parse_args(sys.argv)
    if not config:
        # We exit the program after having generated a config file or got an error
        return

    services = ServiceGroup(WebPingerSvc(),
                            PostgreSQLInserterSvc())

    print(f'{len(services.services)} services are going to start.')
    print(f'Press Ctrl + C, or send a SIGINT (2) to the PID {os.getpid()}, when you want to stop them.')

    # We want to stop our activities gracefully by catching the Ctrl+C signal,
    # otherwise a KeyboardInterrupt exception can be raised anywhere in the main thread.
    # See: https://docs.python.org/3/library/signal.html#note-on-signal-handlers-and-exceptions
    def handler(signum, frame):  # pylint: disable=unused-argument
        logging.debug('Signal handler called with signal: %s', signum)
        services.stop()
    signal.signal(signal.SIGINT, handler)

    services.reload(config)
    services.start()

    tasks = asyncio.all_tasks()
    # We compare to one and not to zero, because there is this current task counting for one, on top of the possible other tasks created by the services.
    # Hence, when there is only one task left, which is us here, that means the services are all stopped and we can break the loop to stop this very last task.
    # If we don't wait for other tasks, the program will finish prematurally because the other tasks cannot run outside of the event loop maintained by asyncio.run(main()) at the bottom of this file.
    while len(tasks) > 1:
        logging.debug('Remaining tasks: %s', len(tasks))
        await asyncio.wait(tasks, timeout=1)
        tasks = asyncio.all_tasks()


def main():
    """ Main entry point """
    asyncio.run(amain())
