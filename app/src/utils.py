from flask import request
import logging
import re

logger = logging.getLogger(__name__)

class Storage:
    CARRIED = 'carried'
    LOCAL = 'local'
    UNIVERSAL = 'universal'
    TYPES = [CARRIED, LOCAL, UNIVERSAL]

class Pile:
    PILE_TYPE = None  # specify in child classes
    def __init__(self, item=None, container=None):
        from .item import Item
        self.item = item if item else Item()
        self.container = container  # character or location where item is
        self.quantity = 0

class RequestHelper:
    """Verify input from request such as integer field on form."""
    def __init__(self, source):
        """
        :param source: 'args' for url params after "?"
                       'form' for POST data
        """
        self.source = source

    def _get_source(self):
        if self.source not in ['form', 'args']:
            raise ValueError("Source must be 'form' or 'args'")
        return getattr(request, self.source)

    def _get_from_request(self, key):
        """Retrieve a value from the request."""
        return self._get_source().get(key, '')

    def has_key(self, key):
        return key in self._get_source()

    def debug(self):
        logger.debug(self._get_source())

    def get_str(self, key, default=''):
        """Retrieve a string from the request.
        No special checks are needed -- just a thin wrapper method.
        """
        return self._get_from_request(key) or default

    def get_list(self, key):
        """Retrieve a list of results with the same name.
        Naming with "[]" is a convention to indicate such items.
        No special checks are needed -- just a thin wrapper method.
        """
        return self._get_source().getlist(key)

    def get_int(self, key, default=0):
        """Retrieve an integer value from the request."""
        value_str = self._get_from_request(key)
        try:
            return int(value_str) if value_str else default
        except ValueError:
            return default

    def get_float(self, key, default=0.0):
        """Retrieve a floating point value from the request."""
        value_str = self._get_from_request(key)
        try:
            return float(value_str) if value_str else default
        except ValueError:
            return default

    def get_bool(self, key, default=False):
        """Retrieve a boolean value from the request."""
        value_str = self._get_from_request(key)
        try:
            return bool(value_str) if value_str else default
        except ValueError:
            return default

def dec2str(value):
    """Convert the value to a string and remove trailing ".0" if present."""
    if value is None or value == '':
        return ''
    return re.sub(r'\.0+$', '', str(value))
