""" This module defines the WebPingerSvc service.
    This service is able to:
        - send HTTP GET requests to a list of web sites periodically,
            See the httpx.AsyncClient class.
            See the periodic() function, for the recurring aspect.
        - transform the web response into a metric, including a check of its content against a regular expressions,
            See the WebSiteMeter class.
        - produce a stream of event to Kafka from these mmetrics.
            See the KafkaEnqueuer class, encapsulating a kafka.KafkaProducer object.
            PERFORMANCE: KafkaProducer is internally sending it's events by batches, depending on some time and size parameters.
    The pipeline (ping, transform, send) to process one website once is done by the monitor_website function.
    Finally the WebPingerSvc class is orchestrating all these objects with:
        - their initialization,
        - the service loop, managing a few asynchronous aspects,
        - their resource releasing.
"""

import asyncio
import datetime
import json
import logging
import re
import sched
import socket

import httpx
import kafka

from webmon.service import Service


class WebSiteMeter:  # pylint: disable=too-few-public-methods
    """ Transform a web response into a metric, including a check of its content against a regular expressions. """
    def decode(self,  # pylint: disable=too-many-arguments
               response, url, regex,
               timestamp, source):
        """ Transform a web response with other parameters into a metric dictionnaly. """
        return {'rsp_tm': response.elapsed // datetime.timedelta(microseconds=1),
                'status': response.status_code,
                'url': str(url),
                'check': regex.search(response.text) is not None,
                'timestamp': str(timestamp),
                'source': source}


class KafkaEnqueuer:
    """ KafkaEnqueuer is encapsulating a kafka.KafkaProducer object.
        This encapsulation is easing the testing by replacing the kafka.KafkaProducer by a mock. """
    def __init__(self, producer_cls=kafka.KafkaProducer, **kwargs):
        logging.debug('Creating KafkaProducer...')
        self.producer = producer_cls(**kwargs)
        logging.info('KafkaProducer created')

    async def add(self, metric, topic):
        """ Send one JSON metric to a Kafka topic.
            PERFORMANCE: the KafkaProducer is sending events my batches, depeding on time and size settings. """
        self.producer.send(topic, json.dumps(metric).encode('utf-8'))

    def close(self):
        """ Close the KafkaProducer connection. """
        self.producer.close()


async def monitor_website(client, url,  # pylint: disable=too-many-arguments
                          meter, regex,
                          enqueuer, topic):
    """ monitor_website is performing the steps (ping, transform, send) to process one website once.
        This function is made re-entrant, by relying only on it's parameter, to perform its job.
        This function can be easily tested by putting mocks in its parameters. """
    try:
        logging.info('Monitoring website: %s', url)
        timestamp = datetime.datetime.now(datetime.timezone.utc)
        response = await client.get(url)
        logging.debug('Website response: %s', response)
        metric = meter.decode(response, url, regex, timestamp, socket.gethostbyname(socket.gethostname()))
        logging.info('Pushing website metric: %s', metric)
        await enqueuer.add(metric, topic)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logging.warning('Exception caught when monitoring website "%s": %s', url, exc)


# Helper function for triggerring periodic events.
# See: https://stackoverflow.com/questions/2398661/schedule-a-repeating-event-in-python-3
def periodic(scheduler,  # pylint: disable=too-many-arguments
             task_registery, task_key,
             interval, action, actionargs=()):
    """ This function is executing an asynchronous action every 'interval' seconds.

        We register the same 'periodic' function with the same arguments to run again in 'interval' seconds.
        At that time it will register itself again and so on.
        Hence we create a periodic execution of this function.
        This function also executes 'action' each time it wakes up, which is our ultimate goal.
        On top of the initial contribution, linked above, we need to register a reference of the running task.
        Otherwise our task might be garbage collected even before running.
        See: https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task """
    scheduler.enter(interval, 1, periodic, (scheduler, task_registery, task_key, interval, action, actionargs))
    task_registery[task_key] = asyncio.create_task(action(*actionargs))


class WebPingerSvc(Service):  # pylint: disable=too-many-instance-attributes
    """ WebPingerSvc is orchestrating the data flow to:
        - ping the websites regularly,
        - transform the web responses into metrics, while performing a few checks,
        - produce a stream of Kafka events and send it to a topic. """
    def __init__(self):
        Service.__init__(self)
        self.started = False
        self.scheduler = None
        self.client = None
        self.meter = None
        self.enqueuer = None
        self.__action_tasks = {}

    def start(self):
        websites = self.config.get('websites')
        if not self.started:
            self.started = True
            logging.info('Starting WebPingerSvc')
            self.scheduler = sched.scheduler()
            self.client = httpx.AsyncClient(follow_redirects=True)
            logging.info('httpx client created')
            self.meter = WebSiteMeter()
            self.enqueuer = KafkaEnqueuer(**self.config['kafka-producer'])
            self.__loop_task = asyncio.create_task(self.__loop(websites))  # pylint: disable=unused-private-member,attribute-defined-outside-init
            logging.info('WebPingerSvc started')
            print('The WebPingerSvc service is started.')

    def stop(self):
        if self.started:
            self.started = False
            for event in self.scheduler.queue:
                self.scheduler.cancel(event)
            self.__stop_task = asyncio.create_task(self.client.aclose())  # pylint: disable=unused-private-member,attribute-defined-outside-init
            self.enqueuer.close()
            logging.info('WebPingerSvc stopped')
            print('The WebPingerSvc service is stopped.')

    async def __loop(self, websites):
        for website in websites:
            periodic(self.scheduler, self.__action_tasks, website['url'], website.get('period', 60), self.__action, (website,))
        logging.debug('All website monitoring jobs are ready')
        while self.started:
            # The run() function returns when its queue is empty.
            # That's why we put it in a loop, to allow events yet-to-be-added to be processed too.
            # See: https://github.com/python/cpython/blob/3.11/Lib/sched.py#L136-L137
            self.scheduler.run(blocking=False)
            # If we want an average precision of 1 second, we need a 500 ms sampling.
            # See: https://en.wikipedia.org/wiki/Nyquist%E2%80%93Shannon_sampling_theorem
            await asyncio.sleep(0.5)

    async def __action(self, website):
        await monitor_website(self.client, website['url'],
                              self.meter, re.compile(website.get('regex', '')),
                              self.enqueuer, self.config['kafka-topic'])
