""" Tests of webmon/web_pinger.py """
import asyncio
import datetime
import re

from webmon import web_pinger


class HttpResponseMock:  # pylint: disable=too-few-public-methods
    """ Mock a response of httpx. """
    def __init__(self, *args, **kwargs):  # pylint: disable=unused-argument
        self.elapsed = datetime.timedelta(milliseconds=kwargs.get('elapsed', 42))
        self.status_code = kwargs.get('status_code', 200)
        self.url = kwargs.get('url', 'https://google.com')
        self.text = kwargs.get('text', '<title>Google</title>')


class HttpAsyncClientMock:
    """ Mock an asyncrone client of httpx. """
    def __init__(self, *args, **kwargs):  # pylint: disable=unused-argument
        self.__mapping = {}

    async def get(self, url):  # pylint: disable=missing-function-docstring
        return self.__mapping[url]

    def close(self):  # pylint: disable=missing-function-docstring
        pass

    def input(self, mapping):
        """ Give the wanted http responses for urls as {"url": HttpResponseMock} """
        self.__mapping = mapping


class WebSiteMeterMock:  # pylint: disable=too-few-public-methods
    """ Mock the component that analyses an http response and produces a specific metric. """
    def decode(self,  # pylint: disable=too-many-arguments
               response, url, regex,
               timestamp, source):  # pylint: disable=unused-argument
        """ The value of 'timestamp' and 'source' are ignored for reproducibility purpose. """
        return {
            'rsp_tm': response.elapsed // datetime.timedelta(microseconds=1),
            'status': response.status_code,
            'url': str(url),
            'check': regex.search(response.text) is not None,
            # Obfuscate the fields that can change depending on who/when is running the test
            'timestamp': '2023-04-16 09:02:42.068288+00:00',
            'source': '192.168.1.6'}


class KafkaProducerMock:
    """ Mock a KafkaProducer, allowing to extract its output. """
    def __init__(self, *args, **kwargs):  # pylint: disable=unused-argument
        self.__sent = []

    def send(self, topic, message):  # pylint: disable=missing-function-docstring
        self.__sent.append((topic, message))

    def close(self):  # pylint: disable=missing-function-docstring
        pass

    def output(self):
        """ Retreive the list of sent tuples (topic, message). """
        sent, self.__sent = self.__sent, []
        return sent


class Test_monitor_website:  # pylint: disable=invalid-name,missing-class-docstring
    def setup_method(self, method):  # pylint: disable=unused-argument
        """ Called before each method.
            See: https://docs.pytest.org/en/6.2.x/xunit_setup.html """
        # pylint: disable=attribute-defined-outside-init
        self.client = HttpAsyncClientMock()
        self.client.input({
            'https://google.com': HttpResponseMock(),
            'https://google.com/dummy': HttpResponseMock(status_code=404),
            'https://bing.com': HttpResponseMock(text='<title>Bing</title>'),
        })
        self.meter = WebSiteMeterMock()
        self.enqueuer = web_pinger.KafkaEnqueuer(producer_cls=KafkaProducerMock)

    def teardown_method(self, method):  # pylint: disable=unused-argument
        """ Called after each method.
            See: https://docs.pytest.org/en/6.2.x/xunit_setup.html """
        self.client.close()
        self.enqueuer.close()

    def test_invalid_url(self):
        """ The URL https://1234567890google.com does not exist. """
        asyncio.run(web_pinger.monitor_website(
            self.client, 'https://1234567890google.com',
            self.meter, re.compile('<title>Google</title>'),
            self.enqueuer, 'default_topic'))
        assert self.enqueuer.producer.output() == []  # nosec assert_used

    def test_checked_html(self):
        """ The content of https://google.com does match the "<title>Google</title>" regex. """
        asyncio.run(web_pinger.monitor_website(
            self.client, 'https://google.com',
            self.meter, re.compile('<title>Google</title>'),
            self.enqueuer, 'default_topic'))
        assert self.enqueuer.producer.output() == [  # nosec assert_used
            ('default_topic', b'{"rsp_tm": 42000, "status": 200, "url": "https://google.com", "check": true, "timestamp": "2023-04-16 09:02:42.068288+00:00", "source": "192.168.1.6"}')]

    def test_404(self):
        """ The 'dummy' resource in https://google.com/dummy does not exist. """
        asyncio.run(web_pinger.monitor_website(
            self.client, 'https://google.com/dummy',
            self.meter, re.compile('<title>Google</title>'),
            self.enqueuer, 'default_topic'))
        assert self.enqueuer.producer.output() == [  # nosec assert_used
            ('default_topic', b'{"rsp_tm": 42000, "status": 404, "url": "https://google.com/dummy", "check": true, "timestamp": "2023-04-16 09:02:42.068288+00:00", "source": "192.168.1.6"}')]

    def test_unchecked_html(self):
        """ The content of the valid URL https://bing.com does not match "<title>Google</title>" """
        asyncio.run(web_pinger.monitor_website(
            self.client, 'https://bing.com',
            self.meter, re.compile('<title>Google</title>'),
            self.enqueuer, 'default_topic'))
        assert self.enqueuer.producer.output() == [  # nosec assert_used
            ('default_topic', b'{"rsp_tm": 42000, "status": 200, "url": "https://bing.com", "check": false, "timestamp": "2023-04-16 09:02:42.068288+00:00", "source": "192.168.1.6"}')]
