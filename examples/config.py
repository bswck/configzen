from __future__ import annotations
from ipaddress import IPv4Address, IPv6Address
from configzen import ConfigModel, ConfigMeta, ConfigField


class DatabaseConfig(ConfigModel):
    host: IPv4Address | IPv6Address
    port: int
    user: str
    password: str = ConfigField(exclude=True)
    test: DatabaseConfig | None = None

    class Config(ConfigMeta):
        resource = "examples/database.yaml"
        env_prefix = "DB_"


db_config = DatabaseConfig.load()
# Optionally change your config or just persist it, excluding the `password` field.
print(db_config.save())
