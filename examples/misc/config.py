import os
from ipaddress import IPv4Address, IPv6Address

from configzen import ConfigModel, ConfigMeta, ConfigField


class DatabaseConfig(ConfigModel):
    host: IPv4Address | IPv6Address
    port: int
    user: str
    password: str = ConfigField(exclude=True)

    class Config(ConfigMeta):
        resource = "database.yaml"
        env_prefix = "DB_"


os.environ["DB_PASSWORD"] = "12345"
db_config: DatabaseConfig = DatabaseConfig.load()

print(db_config.host)
db_config.host = "0.0.0.0"
print(db_config.host)

print(db_config.at("port").reload())
print(db_config)
print(db_config.at("host").reload())
print(db_config)

db_config.port = 1234

print(db_config.reload())

db_config.host = "0.0.0.0"
db_config.port = 443
print(db_config)

print(db_config.at("host").save())
print(db_config.reload())

print(db_config.save())
