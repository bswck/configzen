from configzen import Config


class ConnSpec(Config):
    host: str
    port: int
    user: str
    password: str
    database: str


class Point2D(Config):
    x: int
    y: int


class MyConfig(Config):
    spec: ConnSpec
    point: Point2D = Point2D(0, 0)


config = MyConfig.load("connspec.yaml")
original = config.original

print(original)

config.point.x += 100
config.point.y -= 1

config.at("point.x").save()  # leaving spec & point.y unchanged
config(**original).save()  # rollback
print(original)
