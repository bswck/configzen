
from configzen import Config, dataclass_load, loader


class ConnSpec(Config):
    host: str
    port: int
    user: str
    password: str
    database: str


@loader(dataclass_load)
class NestedConfig(Config):
    nested_key: str


class Point2D(Config):
    nested_config: NestedConfig
    x: int = 1
    y: int = 1


class MyConfig(Config):
    spec: ConnSpec
    point: Point2D = Point2D(
        nested_config=NestedConfig(nested_key='nested_value')
    )


config = MyConfig.load('connspec.yaml', create_missing=True)
config.point.x += 1
config.point.y -= 1
