import yaml

from configzen import Engine


class YamlEngine(Engine):
    name = "yaml"

    def __init__(self, sections=None, **options) -> None:
        super().__init__(sections, **options)

    def load(self, blob, defaults=None):
        if defaults is None:
            defaults = {}
        loaded = None
        if blob:
            loaded = yaml.load(blob, Loader=yaml.SafeLoader)
        return defaults | (loaded or {})

    def _dump(self, config):
        return yaml.dump(config, **self.engine_options)

    # todo: add explicit support for all the weird yaml features
