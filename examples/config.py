import dataclasses

from configzen import Config, ConfigDataclass


@dataclasses.dataclass
class Test(ConfigDataclass):
    example: int
    config: list[str]
    dicts: list[dict[str]]


config = Config(
    'config.yaml',
    test=Test
)
config.test.config.extend(range(100))
config.test.example += 1
config.save()

