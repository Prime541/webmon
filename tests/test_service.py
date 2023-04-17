""" Tests of webmon/service.py """
from webmon.service import Service
from webmon.service import ServiceGroup


class ServiceMock(Service):
    """ Mock a service to keep track of its calls. """
    def __init__(self):
        Service.__init__(self)
        self.calls = []

    def reload(self, config):
        Service.reload(self, config)
        self.calls.append('reload')

    def start(self):
        self.calls.append('start')

    def stop(self):
        self.calls.append('stop')


class Test_ServiceGroup:  # pylint: disable=invalid-name,missing-class-docstring
    def setup_method(self, method):  # pylint: disable=unused-argument
        """ Called before each method.
            See: https://docs.pytest.org/en/6.2.x/xunit_setup.html """
        # pylint: disable=attribute-defined-outside-init
        self.svc_a = ServiceMock()
        self.svc_b = ServiceMock()
        self.svc_group = ServiceGroup(self.svc_a, self.svc_b)

    def test_config(self):
        """ Verify that the 'config' is well managed. """
        self.svc_group.reload(42)
        assert self.svc_a.config == 42  # nosec assert_used
        assert self.svc_b.config == 42  # nosec assert_used
        self.svc_b.config = 541
        assert self.svc_a.config == 42  # nosec assert_used
        assert self.svc_b.config == 541  # nosec assert_used
        self.svc_group.reload(123)
        self.svc_group.start()
        self.svc_group.stop()
        assert self.svc_a.config == 123  # nosec assert_used
        assert self.svc_b.config == 123  # nosec assert_used

    def test_sequence(self):
        """ Verify that all the services are forwarded the same sequence of calls. """
        self.test_config()
        assert self.svc_a.calls == self.svc_b.calls  # nosec assert_used
