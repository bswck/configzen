from __future__ import annotations

import dataclasses
from typing import Any, cast

from pydantic import BaseModel
from pydantic.json import ENCODERS_BY_TYPE

from configzen.engine import Engine
from configzen.errors import UninstalledEngineError

try:
    import yaml
except ModuleNotFoundError:
    raise UninstalledEngineError(engine_name="yaml", library_name="pyyaml") from None


from yaml.representer import Representer, Node


def _represent_dataclass(representer: Representer, value: Any) -> Node:
    return representer.represent_dict(dataclasses.asdict(value))


def _represent_model(representer: Representer, value: Any) -> Node:
    return representer.represent_dict(value.dict())


def _represent_default(representer: Representer, value: Any) -> Node:
    if dataclasses.is_dataclass(value):
        return _represent_dataclass(representer, value)    
    return representer.represent_undefined(value)


def _represent_object(representer: Representer, value: Any) -> Node:
    if dataclasses.is_dataclass(value):
        return _represent_dataclass(representer, value)    
    return representer.represent_object(value)


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

        for data_type, encoder in ENCODERS_BY_TYPE.items():
            self.dumper.add_representer(
                cast(Any, data_type), lambda representer, value: (
                    representer.represent_data(encoder(value))
                )
            )

        self.dumper.add_representer(None, _represent_default)
        self.dumper.add_multi_representer(BaseModel, _represent_model)
        self.dumper.add_multi_representer(object, _represent_object)

    def load(self, *, model_class, blob):
        loaded = None
        if blob:
            loaded = yaml.load(blob, Loader=self.loader)
        return model_class.parse_obj(loaded)

    def dump_object(self, obj):
        return yaml.dump(obj, Dumper=self.dumper, **self.engine_options)
