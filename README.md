# configzen
_configzen_ – managing configuration files easily.

## 📖 About
_configzen_ combines the power of [pydantic](https://pydantic-docs.helpmanual.io/) 
and [anyconfig](https://github.com/ssato/python-anyconfig) to provide the most simplistic
way on Earth of managing configuration files in your Python projects.

Thanks to this, instead of manually using 
`pyyaml` for YAML configuration files, `configparser` for `ini` files, `json` for JSON files, etc. 
you can create a data model of your configuration and let _configzen_ do the rest and provide you 
with some extra features on top of that, such as both synchronous and asynchronous, 
preferably full or partial reloading and saving of your structured configuration.

Let's see how it looks like in practice. Let's say you have a YAML configuration file like this:
```yaml
# database.yaml
host: localhost
port: 5432
user: postgres
```
You can create a data model for it like this:

```python
# config.py
from ipaddress import IPv4Address, IPv6Address
from typing import Literal
from configzen import ConfigModel, Meta, Field


class DatabaseConfig(ConfigModel):
    host: IPv4Address | IPv6Address | Literal['localhost']
    port: int
    user: str
    password: str = Field(exclude=True)

    class Config(Meta):
        resource = "database.yaml"
        env_prefix = "DB_"


db_config = DatabaseConfig.load()
# Optionally change your config or just persist it, excluding the `password` field.
db_config.save()
```

As simple as that!
This way, you can load your configuration from a file or environment variables.

Pydantic will take care of parsing and validating the loaded data.

You can now use the `db_config` object to access the configuration values:

```python
>>> db_config.host
'localhost'
```

modify them, if the Pydantic model allows it:

```python
>>> db_config.host = "newhost"
>>> db_config.host
'newhost'
```

as well as reload particular values, without touching the rest of the configuration:

```python
>>> db_config.at('host').reload()
```

or reload the whole configuration:

```python
>>> db_config.reload()
'localhost'
```

or save a particular value:

```python
>>> db_config.at('host').save()
```

or save the whole configuration:

```python
>>> db_config.save()
```

## Setup
## License
[MIT License](https://choosealicense.com/licenses/mit/)
## Contributing
## 📧 Contact
* [bswck](https://github.com/bswck)
## 🔗 Related Projects 
