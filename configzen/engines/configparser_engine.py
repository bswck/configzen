from io import StringIO
from typing import Any

from configzen import Engine

import configparser


class ConfigParserEngine(Engine):
    name = 'configparser'

    def __init__(self, parser_factory=configparser.ConfigParser, **export_options):
        self.parser_factory = parser_factory
        self.export_options = export_options

    def load(self, serialized_data, defaults=None):
        if defaults is None:
            defaults = {}
        data = self.parser_factory(defaults=defaults)
        data.read_string(serialized_data)
        return data

    def dump(self, config: dict[str, Any]):
        data = self.parser_factory()
        data.read_dict(config)
        dump = StringIO()
        data.write(dump)
        return dump.read()
