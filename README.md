# configzen
_configzen_ – managing configuration files easily.
Currently under development, not ready for production use.

⭐ Contributions welcome! ⭐

## What is this?
For a more precise and also philosophic explanation, see the wiki article [configzen – explanation without code](https://github.com/bswck/configzen/wiki/configzen-%E2%80%93-explanation-without-code).

_configzen_ combines the power of [pydantic](https://pydantic-docs.helpmanual.io/) 
and [anyconfig](https://github.com/ssato/python-anyconfig) to provide the most simplistic
way on Earth of managing configuration files in your Python projects.

Thanks to this, instead of manually using 
`pyyaml` for YAML configuration files, `configparser` for INI files, `json` for JSON files, etc. 
you can create a data model of your configuration and let _configzen_ do the rest and provide you 
with some extra features on top of that, such as both synchronous and asynchronous, 
preferably full or partial reloading and saving of your structured configuration.

Let's see how it looks like in practice. Let's say you have a YAML configuration file like this:
```yaml
# database.yaml
host: 127.0.0.1
port: 5432
user: postgres
```
You can create a data model for it like this:

```python
# config.py
from ipaddress import IPv4Address, IPv6Address
from configzen import ConfigModel, ConfigMeta, ConfigField


class DatabaseConfig(ConfigModel):
    host: IPv4Address | IPv6Address
    port: int
    user: str
    password: str = ConfigField(exclude=True)
    
    class Config(ConfigMeta):
        resource = "database.yaml"
        validate_assignment = True
        env_prefix = "DB_"


db_config = DatabaseConfig.load()
```

As simple as that!
This way, you can load your configuration from a file and from environment variables.

[pydantic](https://docs.pydantic.dev/latest/) will take care of parsing and validating the loaded data.

You can now use the `db_config` object to access the configuration values:

```python
>>> db_config.host
IPv4Address('127.0.0.1')
```

modify them, if the Pydantic model allows it:

```python
>>> db_config.host = "0.0.0.0"
>>> db_config.host
IPv4Address('0.0.0.0')
```

as well as reload particular values, without touching the rest of the configuration:

```python
>>> db_config.at("host").reload()
IPv4Address('127.0.0.1')
```

or reload the whole configuration:

```python
>>> db_config.reload()
DatabaseConfig(host=IPv4Address('127.0.0.1'), port=5432, user='postgres', password='password')
```

or save a particular value:

```python
>>> db_config.host = "0.0.0.0"
>>> db_config.port = 443
>>> db_config
DatabaseConfig(host=IPv4Address('0.0.0.0'), port=443, user='postgres', password='password')
>>> db_config.at("host").save()
40
>>> db_config.reload()
DatabaseConfig(host=IPv4Address('0.0.0.0'), port=5432, user='postgres', password='password')
```

or save the whole configuration:

```python
>>> db_config.save()
39
```

## Setup
For using _configzen_ in your project, you need to install it first:
```bash
pip install configzen
```

For development, you can clone the repository and install its dependencies with [poetry](https://python-poetry.org/):
```bash
poetry install --with dev
```

## License
[MIT License](https://choosealicense.com/licenses/mit/)

## Contributing
Contributions are welcome! Feel free to [open an issue](https://github.com/bswck/configzen/issues/new/choose) 
or [submit a pull request](https://github.com/bswck/configzen/compare).

## Credits
* [@Lunarmagpie](https://github.com/Lunarmagpie) for _crucial_ design tips and ideas.
 
## Author
* [bswck](https://github.com/bswck) (contact: bswck.dev@gmail.com or via [Discord](https://discord.com/) `bswck#8238`)
