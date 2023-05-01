import os
from typing import Any

from configzen import Engine

if not os.getenv('CONFIGZEN_DISABLE_ORJSON'):
    try:
        import orjson as json
    except ImportError:
        import json

try:
    import jsonschema

    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False
    jsonschema = None  # type: ignore


class JSONEngine(Engine):
    name = 'json'

    def __init__(self, schema, json_schema=None, json_schema_validator=None, **options):
        super().__init__(schema, **options)
        if json_schema and not JSONSCHEMA_AVAILABLE:
            raise RuntimeError('jsonschema is not available')
        self.json_schema = json_schema

        self.json_schema_validator = json_schema_validator

    def load(self, blob, defaults=None):
        if defaults is None:
            defaults = {}
        config = defaults | json.loads(blob, **self.engine_options)
        if self.json_schema:
            self.validate(config)
        return config

    def validate(self, data):
        if JSONSCHEMA_AVAILABLE:
            jsonschema.validate(  # type: ignore
                data, self.json_schema, cls=self.json_schema_validator
            )

    def _dump(self, config: dict[str, Any]):
        if self.json_schema:
            self.validate(config)
        return json.dumps(config, **self.engine_options)
