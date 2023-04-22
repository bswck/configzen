import dataclasses

from configzen.engine import converter


class Dataclass:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        converter(dataclasses.asdict)(cls)

    @classmethod
    def __configzen_create__(cls, item, value):
        return cls(**value)  # type: ignore
