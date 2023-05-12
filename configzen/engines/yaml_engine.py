from __future__ import annotations

from typing import Any

import pydantic.json

from configzen.engine import Engine
from configzen.errors import UninstalledEngineError

try:
    import yaml
except ModuleNotFoundError:
    raise UninstalledEngineError(engine_name="yaml", library_name="pyyaml") from None


class YAMLEngine(Engine):
    name = "yaml"
    loader: type[
        yaml.Loader | yaml.CLoader | yaml.SafeLoader | yaml.CSafeLoader
        | yaml.FullLoader | yaml.CFullLoader | yaml.UnsafeLoader | yaml.CUnsafeLoader
    ]
    dumper: type[
        yaml.Dumper | yaml.CDumper | yaml.SafeDumper | yaml.CSafeDumper
    ]

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        
        if options.get("c_impl"):
            if options.get("safe"):
                self.loader = yaml.CSafeLoader
                self.dumper = yaml.CSafeDumper
            else:        
                self.loader = yaml.CLoader       
                self.dumper = yaml.CDumper
        else:
            if options.get("safe"):
                self.loader = yaml.SafeLoader
                self.dumper = yaml.SafeDumper
            else:        
                self.loader = yaml.Loader       
                self.dumper = yaml.Dumper

        for data_type, encoder in pydantic.json.ENCODERS_BY_TYPE.items():
            self.dumper.add_representer(
                data_type, 
                lambda representer, value: representer.represent_data(encoder(value))
            )

    def load(self, *, model_class, blob):
        loaded = None
        if blob:
            loaded = yaml.load(blob, Loader=self.loader)
        return model_class.parse_obj(loaded)

    def dump(self, model):
        self.dumper.add_representer(
            type(model),
            lambda representer, value: representer.represent_data(value.dict()),
        )
        return super().dump(model)

    def dump_mapping(self, mapping):
        return yaml.dump(mapping, Dumper=self.dumper, **self.engine_options)
