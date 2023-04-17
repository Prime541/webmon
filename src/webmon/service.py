""" This module defines services that can be grouped and reloaded """


class Service:
    """ The Service class represents the interface of a configurable service object. """
    def __init__(self):
        self.config = {}

    def reload(self, config):
        """ Store the config object in a config attribute.
            The new config should be taken into account without calling stop(). """
        self.config = config

    def start(self):
        """ Start the underlying implementation.
            Initialize resources. """

    def stop(self):
        """ Stop the underlying implementation.
            Release resources. """


class ServiceGroup(Service):
    """ The ServiceGroup class is propagating its calls to all its children. """
    def __init__(self, *services):
        Service.__init__(self)
        self.services = services

    def reload(self, config):
        for svc in self.services:
            svc.reload(config)
        Service.reload(self, config)

    def start(self):
        for svc in self.services:
            svc.start()

    def stop(self):
        for svc in self.services:
            svc.stop()
