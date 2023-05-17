from __future__ import annotations
from ipaddress import IPv4Address, IPv6Address
from configzen import ConfigModel, ConfigMeta, ConfigField


class Main(ConfigModel):
    host: IPv4Address | IPv6Address
    port: int
    user: str
    password: str = ConfigField(exclude=True)

    class Config(ConfigMeta):
        env_prefix = "DB_"


class DatabaseConfig(ConfigModel):
    main: Main

    class Config(ConfigMeta):
        resource = "examples/database.yaml"


db_config = DatabaseConfig.load()
print("loaded", db_config)
print(db_config.save())
