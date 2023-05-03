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


loader = lib.DefaultLoader.strict_with_sections(spec=ConnSpec, point=Point2D)
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
config["point"].x += 1
config["point"].y += 1
config["spec"].host = "newhost"
print(lib.save(config.section("point")))
