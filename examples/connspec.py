import dataclasses
import typing

import configzen as lib


@dataclasses.dataclass
class ConnSpec(lib.MappingSection):
    host: str
    port: int
    user: str
    password: str
    database: str


@lib.loader(lib.sequence_load)
@lib.converter(lib.sequence_convert)
class Point2D(typing.NamedTuple):
    x: int
    y: int


loader = lib.DefaultLoader.strict_with_schema(spec=ConnSpec, point=Point2D)
defaults = {
    "spec": ConnSpec(
        host="localhost",
        port=5432,
        user="postgres",
        password="postgres",
        database="postgres",
    ),
    "point": Point2D(0, 0),
}
config = lib.Config(
    "connspec.yaml", 
    defaults=defaults, 
    create_missing=True,
    loader=loader,
)
config.load()
config["point"] = Point2D(1, 1)
config["spec"].host = "newhost"
print(lib.save(config.meta("point")))
