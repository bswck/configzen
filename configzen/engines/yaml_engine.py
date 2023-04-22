from typing import Any

from configzen import Engine

import yaml


class YamlEngine(Engine):
    name = 'yaml'

    def __init__(self, **export_options):
        self.export_options = export_options

    def load(self, blob, defaults=None):
        if defaults is None:
            defaults = {}
        return defaults | yaml.load(blob, Loader=yaml.SafeLoader)

    def _dump(self, config: dict[str, Any]):
        return yaml.dump(config, **self.export_options)

    # todo: add explicit support for all the weird yaml features
