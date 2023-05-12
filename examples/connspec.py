from __future__ import annotations

import ipaddress

from pydantic import BaseModel

from configzen import Configuration


class Point2D(BaseModel):
    x: int
    y: int


class MyConfig(Configuration):
    spec: ConnSpec
    point: Point2D = Point2D(x=0, y=0)


class ConnSpec(BaseModel):
    host: ipaddress.IPv4Address
    port: int
    user: str
    password: str
    database: str


config = MyConfig.load("connspec.yaml", create_missing=True)
print(config.spec.dict())
original = config.original
config.spec.host += 1
config.spec.port += 1
config.point.x += 1
config.point.y += 1

# config.__context__.spec.filepath_or_stream = "connspec2.yaml"
print(config.save())
