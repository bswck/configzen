import dataclasses
import configzen as lib


@dataclasses.dataclass
class ConnSpec(lib.DictSection):
    host: str
    port: int
    user: str
    password: str
    database: str
    test: int = 0


config = lib.Config(
    lib.ConfigSpec(
        'connspec.yaml',
        defaults=dict(
            spec=ConnSpec(
                host='localhost',
                port=5432,
                user='postgres',
                password='postgres',
                database='postgres',
                test=0
            )
        ),
        autocreate=True
    ),
    dispatcher=lib.SimpleDispatcher(spec=ConnSpec, hello=list),
)

config['hello'] = ['Hello, people!']
config['spec'].test += 1
print(lib.save(config.meta('hello')))
