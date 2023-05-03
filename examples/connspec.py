import configzen as lib


class ConnSpec(lib.Section):
    host: str
    port: int
    user: str
    password: str
    database: str


class Point2D(lib.Section):
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
