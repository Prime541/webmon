""" This module defines the PostgreSQLInserterSvc service.
    This service is able to:
        - consume a stream of events from Kafka,
            See the KafkaDequeuer class, encapsulating a kafka.KafkaConsumer object.
        - insert corresponding rows in any appropriate PostgreSQL tables,
            Here we have only one trivial transformation to one table.
            It would possible to make it more generic to populate different tables with different transformations.
            See the PgInserter class, encapsulating a psycopg2.connect object.
            See the MetricToTable class, transforming messages from Kafka to SQL queries.
        - create any missing table (and its index and partitions) before inserting rows
    The pipeline (poll, transform, insert) to process one batch of messages is done by the populate_db function.
    Finally the PostgreSQLInserterSvc class is orchestrating all these objects with:
        - their initialization,
        - the service loop, managing a few asynchronous aspects,
        - their resource releasing.
"""

import asyncio
import json
import logging

import kafka
import psycopg2
import psycopg2.sql

from webmon.service import Service


class KafkaDequeuer:
    """ KafkaDequeuer is encapsulating a kafka.KafkaConsumer object.
        This encapsulation is easing the testing by replacing the kafka.KafkaConsumer by a mock. """
    def __init__(self, *topic, consumer_cls=kafka.KafkaConsumer, **kwargs):
        logging.debug('Creating KafkaConsumer...')
        self.consumer = consumer_cls(*topic, **kwargs)
        logging.info('KafkaConsumer created')

    def poll(self):
        """ Return a list of string events. """
        messages = []
        for events in self.consumer.poll().values():
            for event in events:
                messages.append(event.value.decode('utf-8'))
        return messages

    def close(self):
        """ Close the KafkaConsumer connection. """
        self.consumer.close()


class MetricToTable:  # pylint: disable=too-few-public-methods
    """ MetricToTable is transforming messages from Kafka to SQL queries. """
    def __init__(self, table_name):
        self.table_name = table_name

    def decode(self, str_message):
        """ Transform a string metric, a JSON retrieved from Kafka, into an INSERT query. """
        try:
            json_message = json.loads(str_message)
        except json.decoder.JSONDecodeError as exc:
            logging.warning(exc)
            return None, None
        # Avoid SQL injection
        # See: https://realpython.com/prevent-python-sql-injection/
        sql_table_name = psycopg2.sql.Identifier(self.table_name)
        # Beware to not use reserved words, like 'check'.
        # See: https://www.postgresql.org/docs/current/sql-keywords-appendix.html
        prepared_query = psycopg2.sql.SQL("""INSERT INTO {sql_table_name} (time_stamp, source, target, elasped_us, status, valid) VALUES (%s, %s, %s, %s, %s, %s)""").format(
            sql_table_name=sql_table_name
        )
        try:
            values = (json_message['timestamp'], json_message['source'], json_message['url'], json_message['rsp_tm'], json_message['status'], json_message['check'])
        except KeyError as exc:
            logging.warning(exc)
            return None, None
        return prepared_query, values


class PgInserter:
    """ PgInserter is encapsulating a psycopg2.connect object.
        This encapsulation is easing the testing by replacing the psycopg2.connect by a mock. """
    def __init__(self, db_conn_cls=psycopg2.connect, **kwargs):
        logging.debug('Creating PG connection...')
        self.conn = db_conn_cls(**kwargs)
        logging.info('PG connection created')

    def insert(self, prepared_query, list_of_values):
        """ Execute the prepared query with its list of values.
            Notice that 'values' is itself a list of the values for inserting one row.
            When the requested table does not exist, it tries to create it as partitioned. """
        # A connection as a context wraps a transaction (commit or rollback).
        # See: https://www.psycopg.org/docs/connection.html#connection
        if list_of_values:
            logging.debug('Inserting in DB: %s with %s', prepared_query, list_of_values)
            try:
                self.__do_insert(prepared_query, list_of_values)
            except (psycopg2.errors.UndefinedTable,          # pylint: disable=no-member
                    psycopg2.errors.CheckViolation) as exc:  # pylint: disable=no-member
                logging.debug(exc)
                # Extract the table name from the mandatory elements of the "INSERT INTO table_name" query
                # See: https://www.postgresql.org/docs/current/sql-insert.html
                table_name = None
                for sql_part in prepared_query.seq:
                    if isinstance(sql_part, psycopg2.sql.Identifier):
                        table_name = sql_part.string
                        break
                if not table_name:
                    raise
                self.__do_create(table_name)
                # Re-try insert after having created the structure
                self.__do_insert(prepared_query, list_of_values)

    def __do_insert(self, prepared_query, list_of_values):
        """ Execute an INSERT query.
            Raises an exception if the trable does not exist. """
        with self.conn:
            with self.conn.cursor() as curs:
                for values in list_of_values:
                    curs.execute(prepared_query, values)
        logging.info('Inserted rows in DB: %s', len(list_of_values))

    def __do_create(self, table_name):
        """ CREATE a partitioned table. """
        with self.conn:
            with self.conn.cursor() as curs:
                logging.debug('Create table')
                # Avoid SQL injection
                # See: https://realpython.com/prevent-python-sql-injection/
                sql_table_name = psycopg2.sql.Identifier(table_name)
                sql_index_name = psycopg2.sql.Identifier(f'{table_name}_idx_time_stamp')
                sql_partition_name = psycopg2.sql.Identifier(f'{table_name}_default')
                stmt = psycopg2.sql.SQL("""
                    CREATE TABLE IF NOT EXISTS {sql_table_name} (
                        time_stamp timestamp(6),
                        source varchar(1000),
                        target varchar(1000),
                        elasped_us integer,
                        status integer,
                        valid boolean
                    ) PARTITION BY RANGE (time_stamp)""").format(
                    sql_table_name=sql_table_name
                )
                curs.execute(stmt)
                logging.debug('Create index')
                stmt = psycopg2.sql.SQL("""
                    CREATE INDEX IF NOT EXISTS {sql_index_name} ON {sql_table_name} (time_stamp)""").format(
                    sql_index_name=sql_index_name,
                    sql_table_name=sql_table_name
                )
                curs.execute(stmt)
                logging.debug('Create partition')
                stmt = psycopg2.sql.SQL("""
                    CREATE TABLE IF NOT EXISTS {sql_partition_name} PARTITION OF {sql_table_name} DEFAULT""").format(
                    sql_partition_name=sql_partition_name,
                    sql_table_name=sql_table_name
                )
                curs.execute(stmt)

    def close(self):
        """ Close the psycopg2.connect connection. """
        self.conn.close()


def populate_db(dequeuer,
                transformer,
                inserter):
    """ populate_db is performing the steps (poll, transform, insert) to process one batch of messages.
        This function is made re-entrant, by relying only on it's parameter, to perform its job.
        This function can be easily tested by putting mocks in its parameters. """
    messages = dequeuer.poll()
    if messages:
        logging.debug('Polled messages: %s', len(messages))
    queries = {}
    for message in messages:
        logging.debug('Transforming message: %s', message)
        prep_query, values = transformer.decode(message)
        if prep_query:
            logging.debug('Query: %s', prep_query)
            logging.debug('Values: %s', values)
            # Group values by same prepared queries, for optimization purpose.
            hashable_prep_query = str(prep_query)
            if hashable_prep_query in queries:
                queries[hashable_prep_query][1].append(values)
            else:
                queries[hashable_prep_query] = (prep_query, [values])
    if queries:
        logging.debug('Prepared queries: %s', queries)
    for prep_query, params in queries.values():
        inserter.insert(prep_query, params)


class PostgreSQLInserterSvc(Service):
    """ PostgreSQLInserterSvc is orchestrating the data flow to:
        - consume events from Kafka,
        - transform them into appropriate SQL queries,
        - insert data in PostgreSQL (and create partitioned table if needed). """
    def __init__(self):
        Service.__init__(self)
        self.started = False
        self.dequeuer = None
        self.transformer = None
        self.pg_inserter = None

    def start(self):
        if not self.started:
            self.started = True
            logging.info('Starting PostgreSQLInserterSvc')
            self.dequeuer = KafkaDequeuer(self.config['kafka-topic'], **self.config['kafka-consumer'])
            self.transformer = MetricToTable(self.config['pg-table'])
            self.pg_inserter = PgInserter(**self.config['pg-connect'])
            self.__loop_task = asyncio.create_task(self.__loop())  # pylint: disable=unused-private-member,attribute-defined-outside-init
            logging.info('PostgreSQLInserterSvc started')
            print('The PostgreSQLInserterSvc service is started.')

    async def __loop(self):
        while self.started:
            populate_db(self.dequeuer,
                        self.transformer,
                        self.pg_inserter)
            # Make sure that other coroutines can work.
            await asyncio.sleep(0)
        self.dequeuer.close()
        self.pg_inserter.close()
        logging.info('PostgreSQLInserterSvc stopped')
        print('The PostgreSQLInserterSvc service is stopped.')

    def stop(self):
        if self.started:
            self.started = False
