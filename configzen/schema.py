import dataclasses
import typing

from configzen.engine import to_dict


# @typing.dataclass_transform
class ConfigDataclass:
    @classmethod
    def __configzen_create__(cls, key, data, parent):
        return cls(**data)

    def __configzen_to_dict__(self):
        return dataclasses.asdict(self)
