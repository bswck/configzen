from collections.abc import ByteString, MutableMapping, Callable
from typing import Any

import yaml

from configzen import Engine


class YamlEngine(Engine):
    name = "yaml"

    def __init__(
        self,
        sections: dict[str, Callable[[Any], Any]] | None = None,
        **options: Any
    ) -> None:
        super().__init__(sections, **options)

    def load(
        self,
        blob: str | ByteString | None,
        defaults: MutableMapping[str, Any] | None = None,
    ) -> MutableMapping[str, Any]:
        if defaults is None:
            defaults = {}
        loaded = None
        if blob:
            loaded = yaml.load(blob, Loader=yaml.SafeLoader)
        return defaults | (loaded or {})

    def _dump(self, config: MutableMapping[str, Any]) -> str | ByteString:
        return yaml.dump(config, **self.engine_options)

    # todo: add explicit support for all the weird yaml features
