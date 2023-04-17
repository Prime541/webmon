""" Tests of webmon/db_inserter.py """
import contextlib

import psycopg2

from webmon import db_inserter


def sql_human_readable(composite):
    """ Transform an SQL statement from its safe composite form
        to a serialized one, more readable by a human.
        For debug purpose only. """
    if isinstance(composite, (psycopg2.sql.SQL, psycopg2.sql.Identifier)):
        return composite.string
    if isinstance(composite, psycopg2.sql.Composed):
        return ''.join(sql_human_readable(comp) for comp in composite.seq)
    return composite


class KafkaEventMock:  # pylint: disable=too-few-public-methods
    """ KafkaEventMock represents one event from Kafka.
        Its 'value' attribute is the message as bytes. """
    def __init__(self, message):
        """ message should be of type bytes """
        self.value = message


class KafkaConsumerMock:
    """ Mock a KafkaConsumer, allowing to set next polled events. """
    def __init__(self, *args, **kwargs):  # pylint: disable=unused-argument
        self.__received = {}

    def poll(self):  # pylint: disable=missing-function-docstring
        received, self.__received = self.__received, {}
        return received

    def close(self):  # pylint: disable=missing-function-docstring
        pass

    def input(self, messages):
        """ Give the messages, as a list of bytes, to be polled next. """
        self.__received = {'partition0': [KafkaEventMock(m) for m in messages]}


class PgCursorMock(contextlib.AbstractContextManager):
    """ Mock a cursor, allowing it to simulate a Database exception. """
    def __init__(self, db_conn):
        self.db_conn = db_conn
        self.success = True
        self.__sent = []

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.success = exc_type is None
        return False

    def execute(self, query, params=None):  # pylint: disable=missing-function-docstring
        self.__sent.append((sql_human_readable(query), params))
        if self.db_conn.simulate_exceptions > 0:
            self.db_conn.simulate_exceptions -= 1
            raise psycopg2.errors.UndefinedTable(self.db_conn.simulate_exceptions + 1)  # pylint: disable=no-member

    def output(self):
        """ Retrieve the list of executed queries, whatever they succeeded or failed.
            Actually each query is represented as a tuple of (query, params),
            where params can be None or a list of values. """
        sent, self.__sent = self.__sent, []
        return sent


class PgConnectionMock(contextlib.AbstractContextManager):
    """ Mock a connection, allowing its cursors to simulate an exception. """
    def __init__(self, *args, **kwargs):  # pylint: disable=unused-argument
        self._status = 'connected'
        self.__cursors = []
        self.simulate_exceptions = 0

    def __enter__(self):
        self._status = 'transaction'
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._status = 'committed' if exc_type is None else 'rolled back'
        return False

    def cursor(self):  # pylint: disable=missing-function-docstring
        cursor = PgCursorMock(self)
        self.__cursors.append(cursor)
        return cursor

    def close(self):  # pylint: disable=missing-function-docstring
        self._status = 'closed'

    def output(self):
        """ Retreive the list of successfully committed queries as (query, params). """
        cursors, self.__cursors = self.__cursors, []
        sent = []
        if self._status == 'committed':
            for cursor in cursors:
                if cursor.success:
                    sent += cursor.output()
        return sent


class Test_populate_db:  # pylint: disable=invalid-name,missing-class-docstring
    def setup_method(self, method):  # pylint: disable=unused-argument
        """ Called before each method.
            See: https://docs.pytest.org/en/6.2.x/xunit_setup.html """
        # pylint: disable=attribute-defined-outside-init
        self.dequeuer = db_inserter.KafkaDequeuer(consumer_cls=KafkaConsumerMock)
        self.dequeuer.consumer.input((
            b'{"rsp_tm": 42000, "status": 200, "url": "https://google.com", "check": true, "timestamp": "2023-04-16 09:02:42.068288+00:00", "source": "192.168.1.6"}',
            b'{"rsp_tm": 42000, "status": 404, "url": "https://google.com/dummy", "check": true, "timestamp": "2023-04-16 09:02:42.068288+00:00", "source": "192.168.1.6"}',
            b'{"rsp_tm": 42000, "status": 200, "url": "https://bing.com", "check": false, "timestamp": "2023-04-16 09:02:42.068288+00:00", "source": "192.168.1.6"}',
        ))
        self.transformer = db_inserter.MetricToTable('default_table')
        self.inserter = db_inserter.PgInserter(PgConnectionMock)

    def teardown_method(self, method):  # pylint: disable=unused-argument
        """ Called after each method.
            See: https://docs.pytest.org/en/6.2.x/xunit_setup.html """
        self.dequeuer.close()
        self.inserter.close()

    def test_insert_values(self):
        """ We simply try to insert 3 rows in a supposedly existing table. """
        db_inserter.populate_db(self.dequeuer, self.transformer, self.inserter)
        assert self.inserter.conn.output() == [  # nosec assert_used
            ('INSERT INTO default_table (time_stamp, source, target, elasped_us, status, valid) VALUES (%s, %s, %s, %s, %s, %s)',
                ('2023-04-16 09:02:42.068288+00:00', '192.168.1.6', 'https://google.com', 42000, 200, True)),
            ('INSERT INTO default_table (time_stamp, source, target, elasped_us, status, valid) VALUES (%s, %s, %s, %s, %s, %s)',
                ('2023-04-16 09:02:42.068288+00:00', '192.168.1.6', 'https://google.com/dummy', 42000, 404, True)),
            ('INSERT INTO default_table (time_stamp, source, target, elasped_us, status, valid) VALUES (%s, %s, %s, %s, %s, %s)',
                ('2023-04-16 09:02:42.068288+00:00', '192.168.1.6', 'https://bing.com', 42000, 200, False))]

    def test_create_table(self):
        """ We want to simulate an UndefinedTable exception for the first INSERT query.
            We expect db_inserter.PgInserter to catch the exception in order to CREATE the table and re-try the INSERT. """
        self.inserter.conn.simulate_exceptions = 1
        db_inserter.populate_db(self.dequeuer, self.transformer, self.inserter)
        assert self.inserter.conn.output() == [  # nosec assert_used
            ('\n                    CREATE TABLE IF NOT EXISTS default_table (\n                        time_stamp timestamp(6),\n                        source varchar(1000),\n                        target varchar(1000),\n                        elasped_us integer,\n                        status integer,\n                        valid boolean\n                    ) PARTITION BY RANGE (time_stamp)',
                None),
            ('\n                    CREATE INDEX IF NOT EXISTS default_table_idx_time_stamp ON default_table (time_stamp)',
                None),
            ('\n                    CREATE TABLE IF NOT EXISTS default_table_default PARTITION OF default_table DEFAULT',
                None),
            ('INSERT INTO default_table (time_stamp, source, target, elasped_us, status, valid) VALUES (%s, %s, %s, %s, %s, %s)',
                ('2023-04-16 09:02:42.068288+00:00', '192.168.1.6', 'https://google.com', 42000, 200, True)),
            ('INSERT INTO default_table (time_stamp, source, target, elasped_us, status, valid) VALUES (%s, %s, %s, %s, %s, %s)',
                ('2023-04-16 09:02:42.068288+00:00', '192.168.1.6', 'https://google.com/dummy', 42000, 404, True)),
            ('INSERT INTO default_table (time_stamp, source, target, elasped_us, status, valid) VALUES (%s, %s, %s, %s, %s, %s)',
                ('2023-04-16 09:02:42.068288+00:00', '192.168.1.6', 'https://bing.com', 42000, 200, False))]
