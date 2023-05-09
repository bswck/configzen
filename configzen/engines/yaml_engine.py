from __future__ import annotations

from typing import Any, TYPE_CHECKING

from configzen.errors import MissingEngineError

if TYPE_CHECKING:
    pass

try:
    import yaml
except ModuleNotFoundError:
    raise MissingEngineError(
        "yaml engine requires pyyaml to be installed",
    ) from None


from configzen import Engine


class YamlEngine(Engine):
    name = "yaml"

    def load(
        self,
        blob,
        defaults=None,
    ) -> dict[str, Any]:
        if defaults is None:
            defaults = {}
        loaded = None
        if blob:
            loaded = yaml.load(blob, Loader=yaml.SafeLoader)
        return defaults | (loaded or {})

    def _dump(self, config):
        return yaml.dump(config, **self.engine_options)

    # todo: add explicit support for all the weird yaml features
