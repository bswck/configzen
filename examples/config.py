from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from typing import Literal
from configzen import ConfigModel, Meta, ConfigResource, Field


@dataclass
class Point:
    x: int
    y: int


class DatabaseConfig(ConfigModel):
    host: IPv4Address | IPv6Address | Literal['localhost']
    port: int
    user: str
    password: str = Field(exclude=True)
    point: Point = Point(0, 0)

    class Config(Meta):
        resource = ConfigResource(
            "examples/database.json",
            create_if_missing=True
        )
        env_prefix = "DB_"
        arbitrary_types_allowed = True


db_config = DatabaseConfig.load()
db_config.point.x += 100
db_config.point.y += 100
db_config.at("point.x").save()
