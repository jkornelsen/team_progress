import inspect
import locale
import logging
import os
import re

from flask import g, request

logger = logging.getLogger(__name__)

class Storage:
    CARRIED = 'carried'
    LOCAL = 'local'
    UNIVERSAL = 'universal'
    TYPES = [CARRIED, LOCAL, UNIVERSAL]

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
        formatted_str = self._get_from_request(key)
        value_str = unformat_num(formatted_str)
        try:
            return int(value_str) if value_str else default
        except ValueError:
            return default

    def get_float(self, key, default=0.0):
        """Retrieve a floating point value from the request."""
        formatted_str = self._get_from_request(key)
        value_str = unformat_num(formatted_str)
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

    def get_numtup(self, key, default=None, delim=","):
        return NumTup.from_str(
            self._get_from_request(key), default, delim)

    @staticmethod
    def set_num_if_changed(new_formatted, old_unformatted):
        old_formatted = format_num(old_unformatted)
        if new_formatted == old_formatted:
            return old_unformatted  # preserve precision
        return unformat_num(new_formatted)

class NumTup:
    """Manages strings such as '1,2' to store as tuples of
    integers in python, and integer arrays in db.
    """
    def __init__(self, tup=None):
        self.tup = tup if tup else ()

    @classmethod
    def from_str(cls, str_val, default=None, delim=","):
        instance = cls()
        if isinstance(default, tuple):
            instance.tup = default
        if str_val:
            try:
                instance.tup = tuple(
                    map(int, str_val.split(delim)))
            except ValueError:
                pass
        return instance

    @classmethod
    def from_list(cls, the_list):
        return cls(tuple(the_list))

    def __str__(self):
        return ",".join(map(str, self.tup))

    def as_tuple(self):
        return self.tup

    def as_list(self):
        """Suitable for insertion into database or json file."""
        return list(self.tup)

    def as_pg_array(self):
        """Convert to a PostgreSQL array string, for example '{0, 0}'."""
        return f"{{{', '.join(map(str, self.tup))}}}"

    def __add__(self, other):
        if isinstance(other, NumTup):
            return NumTup(self.tup + other.as_tuple())
        return NotImplemented

    def __getitem__(self, key):
        if isinstance(key, slice):
            return NumTup(self.tup[key])
        if isinstance(key, int):
            return self.tup[key]
        return NotImplemented

class LinkLetters:
    """Letters to add before a link for hotkeys."""
    def __init__(self, excluded='o'):
        self.letter_index = 0
        self.letters = [
            chr(c) for c in range(ord('a'), ord('z') + 1)
            if chr(c) not in excluded]
        self.links = {}

    def next(self, link=None):
        """:param link: returns same letter for identical links"""
        if link in self.links:
            return self.links[link]
        if self.letter_index < len(self.letters):
            letter = self.letters[self.letter_index]
            self.letter_index += 1
            if link:
                self.links[link] = letter
            return letter
        return ''

def caller_info(format_str="called from {filename}:{line}"):
    CALLER_INDEX = 2  # the caller of the function that called where we are now
    stack = inspect.stack()
    if len(stack) > CALLER_INDEX:
        frame = stack[CALLER_INDEX]
        return format_str.format(
            filename=os.path.basename(frame.filename),
            line=frame.lineno)
    return ""

def entity_class(typename, entity_classes):
    for entity_cls in entity_classes:
        if typename == entity_cls.typename:
            return entity_cls
    raise ValueError(f"Unexpected type: '{typename}'")

def create_entity(typename, entity_id, entity_classes):
    entity_cls = entity_class(typename, entity_classes)
    return entity_cls(entity_id)

SUFFIXES = [
    '', 'k', 'm', 'b', 't', 'q', 'Q', 's', 'S', 'o', 'n', 'd',
    'u', 'U', 'v', 'V', 'w', 'W', 'x', 'X', 'y', 'Y', 'z', 'Z']

def format_num(value):
    """Convert the value to a string and remove trailing ".0" if present."""
    value_str = str(value).strip()
    if value is None or value_str == '':
        return ''
    try:
        value = float(value)  # Ensure numeric
    except (ValueError, TypeError):
        return ''
    try:
        nformat = g.game_data.overall.number_format
    except AttributeError:
        nformat = ''
    if nformat in ['', None]:
        nformat = 'en_US'
    if nformat == "sci":  # 1.23e6
        formatted = "{:.2e}".format(value)  # 1.23e+06
        # Remove leading 0 and + sign
        formatted = re.sub(r'e\+0?(\d)', r'e\1', formatted)
        formatted = re.sub(r'e-0?(\d)', r'e-\1', formatted)
        return formatted
    if nformat == "abbr":  # 1.23m
        chunk = 0  # which group of 3 digits, as in thousands, millions
        while abs(value) >= 1000 and chunk < len(SUFFIXES) - 1:
            value /= 1000.0
            chunk += 1
        return f"{value:.2f}{SUFFIXES[chunk]}"
    try:
        locale.setlocale(locale.LC_ALL, nformat)
    except locale.Error:
        locale.setlocale(locale.LC_ALL, 'C')  # 1230000
    decimal_places = 0 if value == int(value) else len(
        str(value).split('.')[1])
    try:
        value_str = locale.format_string(
            f"%.{decimal_places}f", value, grouping=True)
        value_str = re.sub(r'(\.\d*?[1-9])0+$', r'\1', value_str)
        value_str = re.sub(r'\.0*$', '', value_str)
    except (ValueError, TypeError):
        return ''
    return value_str

def unformat_num(value_str):
    """Remove formatting from a string. Returns a float."""
    value_str = value_str.strip()
    if value_str == '':
        return 0.0
    # Scientific notation "1.23e6"
    if re.match(r'^\d+(\.\d+)?e[+-]?\d+$', value_str, re.IGNORECASE):
        try:
            return float(value_str)
        except ValueError:
            return 0.0
    # Abbreviated format like "1.23m"
    abbr_map = {
        suffix: 10 ** (3 * chunk)
        for chunk, suffix in enumerate(SUFFIXES[1:])}
    match = re.match(
        rf'^(\d+(\.\d+)?)([{ "".join(SUFFIXES[1:]) }])$', value_str)
    if match:
        number = float(match.group(1))
        suffix = match.group(3)
        return number * abbr_map[suffix]
    # Remove grouping characters like commas or periods
    try:
        normalized_value_str = re.sub(r'[^\d.-]', '', value_str)
        return float(normalized_value_str)
    except (ValueError, TypeError):
        return 0.0
