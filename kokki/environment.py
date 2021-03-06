#!/usr/bin/env python

__all__ = ["Environment"]

import logging
import os
import shutil
import subprocess
from datetime import datetime

from kokki.exceptions import Fail
from kokki.providers import find_provider
from kokki.utils import AttributeDictionary
from kokki.system import System
from kokki.version import long_version

class Environment(object):
    _instances = []

    def __init__(self):
        self.log = logging.getLogger("kokki")
        self.reset()

    def reset(self):
        self.system = System.get_instance()
        self.config = AttributeDictionary()
        self.resources = {}
        self.resource_list = []
        self.delayed_actions = set()
        self.update_config({
            'date': datetime.now(),
            'kokki.long_version': long_version(),
            'kokki.backup.path': '/tmp/kokki/backup',
            'kokki.backup.prefix': datetime.now().strftime("%Y%m%d%H%M%S"),
        })

    def backup_file(self, path):
        if self.config.kokki.backup:
            if not os.path.exists(self.config.kokki.backup.path):
                os.makedirs(self.config.kokki.backup.path, 0700)
            new_name = self.config.kokki.backup.prefix + path.replace('/', '-')
            backup_path = os.path.join(self.config.kokki.backup.path, new_name)
            self.log.info("backing up %s to %s" % (path, backup_path))
            shutil.copy(path, backup_path)

    def update_config(self, attributes, overwrite=True):
        for key, value in attributes.items():
            attr = self.config
            path = key.split('.')
            for pth in path[:-1]:
                if pth not in attr:
                    attr[pth] = AttributeDictionary()
                attr = attr[pth]
            if overwrite or path[-1] not in attr:
                attr[path[-1]] = value

    def run_action(self, resource, action):
        self.log.debug("Performing action %s on %s" % (action, resource))

        provider_class = find_provider(self, resource.__class__.__name__, resource.provider)
        provider = provider_class(resource)
        try:
            provider_action = getattr(provider, 'action_%s' % action)
        except AttributeError:
            raise Fail("%r does not implement action %s" % (provider, action))
        provider_action()

        if resource.is_updated:
            for action, res in resource.subscriptions['immediate']:
                self.log.info("%s sending %s action to %s (immediate)" % (resource, action, res))
                self.run_action(res, action)
            for action, res in resource.subscriptions['delayed']:
                self.log.info("%s sending %s action to %s (delayed)" % (resource, action, res))
            self.delayed_actions |= resource.subscriptions['delayed']

    def _check_condition(self, cond):
        if hasattr(cond, '__call__'):
            return cond()

        if isinstance(cond, basestring):
            ret = subprocess.call(cond, shell=True)
            return ret == 0

        raise Exception("Unknown condition type %r" % cond)

    def run(self):
        with self:
            # Run resource actions
            for resource in self.resource_list:
                self.log.debug("Running resource %r" % resource)

                if resource.not_if is not None and self._check_condition(resource.not_if):
                    self.log.debug("Skipping %s due to not_if" % resource)
                    continue

                if resource.only_if is not None and not self._check_condition(resource.only_if):
                    self.log.debug("Skipping %s due to only_if" % resource)
                    continue

                for action in resource.action:
                    self.run_action(resource, action)

            # Run delayed actions
            while self.delayed_actions:
                action, resource = self.delayed_actions.pop()
                self.run_action(resource, action)

    @classmethod
    def get_instance(cls):
        return cls._instances[-1]

    def __enter__(self):
        self.__class__._instances.append(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__class__._instances.pop()
        return False

    def __getstate__(self):
        return dict(
            config = self.config,
            resources = self.resources,
            resource_list = self.resource_list,
            delayed_actions = self.delayed_actions,
        )

    def __setstate__(self, state):
        self.__init__()
        self.config = state['config']
        self.resources = state['resources']
        self.resource_list = state['resource_list']
        self.delayed_actions = state['delayed_actions']
