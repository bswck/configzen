import dataclasses
import ipaddress

from pydantic import BaseModel
from configzen import ConfigModel


@dataclasses.dataclass
class Point2D:
    x: int
    y: int


class ConnSpec(BaseModel):
    host: ipaddress.IPv4Address = '127.0.0.1'
    port: int = 12345
    user: str = 'default'
    password: str = 'default'
    database: str = 'default'


class MyConfig(ConfigModel):
    spec: ConnSpec = ConnSpec()
    point: Point2D = Point2D(x=0, y=0)


myconf = MyConfig.load("examples/config.yaml", create_if_missing=True)
myconf.save()
