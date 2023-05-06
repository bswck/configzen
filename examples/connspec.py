from configzen import Config


class ConnSpec(Config):
    host: str
    port: int
    user: str
    password: str
    database: str


class NestedConfig(Config):
    nested_key: str


class Point2D(Config):
    nested_config: NestedConfig
    x: int = 1
    y: int = 1


class MyConfig(Config):
    spec: ConnSpec
    point: Point2D


config = MyConfig.load('connspec.yaml')
spec = config.spec
spec.host = 'newhost'
point = config.point
point.x += 1
point.y += 1
point.save()
print(point.nested_config._context.section)
