import os
from collections.abc import ByteString, MutableMapping
from typing import Any

from configzen import Engine

if not os.getenv("CONFIGZEN_DISABLE_ORJSON"):
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
    name = "json"

    def __init__(
        self,
        schema: dict[str, Any] | None = None,
        json_schema: dict[str, Any] = None,
        json_schema_validator: Any =None,
        **options: Any,
    ) -> None:
        super().__init__(schema, **options)
        if json_schema and not JSONSCHEMA_AVAILABLE:
            msg = "jsonschema is not available"
            raise RuntimeError(msg)
        self.json_schema = json_schema

        self.json_schema_validator = json_schema_validator

    def load(
        self,
        blob: str | ByteString | None,
        defaults: MutableMapping[str, Any] | None = None,
    ) -> MutableMapping[str, Any]:
        if defaults is None:
            defaults = {}
        config = defaults | json.loads(blob or "{}", **self.engine_options)
        if self.json_schema:
            self.validate(config)
        return config

    def validate(self, data: MutableMapping[str, Any]) -> None:
        if JSONSCHEMA_AVAILABLE:
            jsonschema.validate(  # type: ignore
                data, self.json_schema, cls=self.json_schema_validator,
            )

    def _dump(self, config: MutableMapping[str, Any]) -> str | ByteString:
        if self.json_schema:
            self.validate(config)
        return json.dumps(config, **self.engine_options)
