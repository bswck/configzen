
import configzen as lib


class ConnSpec(lib.Section):
    host: str
    port: int
    user: str
    password: str
    database: str


class Point2D(lib.Section):
    x: int = 1
    y: int = 1
    

class MyConfig(lib.Config):
    spec: ConnSpec
    point: Point2D


config = MyConfig.load('connspec.yaml')
point = config.point
point.x += 1
point.y += 1
config.save()


