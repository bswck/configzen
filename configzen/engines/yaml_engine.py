from typing import Any

from configzen import Engine

import yaml


class YamlEngine(Engine):
    name = 'yaml'

    def __init__(self, schema=None, **options):
        super().__init__(schema, **options)

    def load(self, blob, defaults=None):
        if defaults is None:
            defaults = {}
        loaded = None
        if blob:
            loaded = yaml.load(blob, Loader=yaml.SafeLoader)
        return defaults | (loaded or {})

    def _dump(self, config: dict[str, Any]):
        return yaml.dump(config, **self.engine_options)

    # todo: add explicit support for all the weird yaml features
