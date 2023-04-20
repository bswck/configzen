from typing import Any

from configzen import Engine

import yaml


class YamlEngine(Engine):
    name = 'yaml'

    def __init__(self, **export_options):
        self.export_options = export_options

    def load(self, serialized_data, defaults=None):
        if defaults is None:
            defaults = {}
        return defaults | yaml.load(serialized_data, Loader=yaml.SafeLoader)

    def dump(self, config: dict[str, Any]):
        return yaml.dump(config, **self.export_options)
