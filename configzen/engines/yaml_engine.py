from __future__ import annotations

from collections.abc import Callable
from typing import Any

from configzen.errors import MissingEngineError

try:
    import yaml
except ModuleNotFoundError:
    raise MissingEngineError(
        "yaml engine requires pyyaml to be installed",
    ) from None


from configzen import Engine


class YamlEngine(Engine):
    name = "yaml"

    def __init__(
        self,
        sections: dict[str, Callable[[Any], Any]] | None = None,
        **options: Any,
    ) -> None:
        super().__init__(sections or {}, **options)

    def load(
        self,
        blob,
        defaults=None,
    ):
        if defaults is None:
            defaults = {}
        loaded = None
        if blob:
            loaded = yaml.load(blob, Loader=yaml.SafeLoader)
        return defaults | (loaded or {})

    def _dump(self, config):
        return yaml.dump(config, **self.engine_options)

    # todo: add explicit support for all the weird yaml features
