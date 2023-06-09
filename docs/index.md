# configzen

_configzen_ – easily create and maintain complex, statically-typed configurations with validation in Python.

## Features

### Managing content

Having a YAML configuration file like this:

```yaml
# database.yml
host: 127.0.0.1
port: 5432
user: postgres
```

You can create a _configzen_ configuration model for it like this:

```python
# model.py
from ipaddress import IPv4Address, IPv6Address
from configzen import ConfigModel, ConfigMeta, ConfigField


class DatabaseConfig(ConfigModel):
    host: IPv4Address | IPv6Address
    port: int
    user: str
    password: str = ConfigField(exclude=True)

    class Config(ConfigMeta):
        resource = "database.yml"
        env_prefix = "DB_"


db_config = DatabaseConfig.load()
```

And you can load your configuration from a file as well as from the environment variables
`DB_HOST`, `DB_PORT`, `DB_USER` and `DB_PASSWORD`. Since `password` is a field created with
the option `exclude=True`, it will not be included in the configuration's exported data: that
guarantees that your password does never leak into `database.yml` on save – but you may still pass it
through an environment variable (here – the mentioned `DB_PASSWORD`). Secret files are also supported,
see [the pydantic documentation](https://docs.pydantic.dev/latest/usage/settings/#secret-support)
for more information.

[pydantic](https://docs.pydantic.dev/latest/) will naturally take care of parsing and validating the loaded data.
Configuration models inherit from the `pydantic.BaseSettings` class, so you can use all of its features:
schema generation, type conversion, validation, etc.

There are additional features brought to you by _configzen_ worth checking out, though.

You can use the `db_config` object defined above to access the configuration values:

```python
>>> db_config.host
IPv4Address('127.0.0.1')
```

modify them, if the pydantic model validation allows
it ([`<Your model>.Config.validate_assignment`](https://docs.pydantic.dev/latest/usage/model_config/#options) will
be `True` by default):

```python
>>> db_config.host = "0.0.0.0"
>>> db_config.host
IPv4Address('0.0.0.0')
```

as well as reload particular values, without touching the rest of the configuration:

```python
>>> db_config.at("port").reload()
5432
>>> db_config
DatabaseConfig(host=IPv4Address('0.0.0.0'), port=5432, user='postgres', password='password')
>>> db_config.at("host").reload()
IPv4Address('127.0.0.1')
>>> db_config
DatabaseConfig(host=IPv4Address('127.0.0.1'), port=5432, user='postgres', password='password')
```

or reload the whole configuration:

```python
>>> db_config.port = 1234
>>> db_config.reload()
DatabaseConfig(host=IPv4Address('127.0.0.1'), port=5432, user='postgres', password='password')
```

or save a particular value, without touching the rest of the configuration:

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

### Preprocessing

To see supported preprocessing directives,
see [Supported preprocessing directives](#supported-preprocessing-directives).

#### Basic usage

Having a base configuration file like this (`base.json`):

```json
{
  "i18n": {
    "language": "en",
    "timezone": "UTC"
  },
  "app": {
    "debug": true,
    "expose": 8000
  }
}
```

create another configuration file like this, overriding desired sections as needed:

```yaml
# production.yml
^extend: base.json

+app:
  debug: false
```

and load the `production.yml` configuration file. No explicit changes to the code indicating the use of the `base.json`
file are needed.

_Note: Using `+` in front of a key will update the section already defined at that key,
instead of replacing it._

Notice how configuration file formats do not matter in _configzen_: you can
extend JSON configurations with YAML, but that might be as well any other format
among the supported ones (see the [Supported file formats](#supported-file-formats) section).

The above example is equivalent to as if you used:

```yaml
# production.yml
i18n:
  language: en
  timezone: UTC
app:
  debug: false
  expose: 8000
```

but with a significant difference: when you save the above configuration, the `^extend` relation to the base
configuration file `base.json` is preserved.
This basically means that changes made in the base configuration file will apply to the configuration model instance
loaded from the `^extend`-ing configuration file.
Any changes made locally to the model will result in `+` sections being automatically added to the exported
configuration data.

#### Supported preprocessing directives

| Directive  | Is the referenced file preprocessed? | Is the directive preserved on export? |
|------------|--------------------------------------|---------------------------------------|
| `^extend`  | Yes                                  | Yes                                   |
| `^include` | Yes                                  | No                                    |
| `^copy`    | No                                   | No                                    |


### Interpolation

#### Basic interpolation

You can use interpolation in your configuration files:

```yaml
cpu:
  cores: 4
num_workers: ${cpu.cores}
```

```python
>>> from configzen import ConfigModel
...
>>> class CPUConfig(ConfigModel):
...     cores: int
...
>>> class AppConfig(ConfigModel):
...     cpu: CPUConfig
...     num_workers: int
...
>>> app_config = AppConfig.load("app.yml")
>>> app_config
AppConfig(cpu=CPUConfig(cores=4), num_workers=4)
```


#### Reusable configuration with namespaces

You can share independent configuration models as namespaces through inclusion:

```yaml
# database.yml
host: ${app_config::db_host}
port: ${app_config::expose}
```

```yaml
# app.yml
db_host: localhost
expose: 8000
```

```python
>>> from configzen import ConfigModel, include
>>> from ipaddress import IPv4Address
>>>
>>> @include("app_config")
... class DatabaseConfig(ConfigModel):
...     host: IPv4Address
...     port: int
...
>>> class AppConfig(ConfigModel):
...     db_host: str
...     expose: int
...
>>> app_config = AppConfig.load("app.yml")
>>> app_config
AppConfig(db_host='localhost', expose=8000)
>>> db_config = DatabaseConfig.load("database.yml")
>>> db_config
DatabaseConfig(host=IPv4Address('127.0.0.1'), port=8000)
>>> db_config.dict()
{'host': IPv4Address('127.0.0.1'), 'port': 8000}
>>> db_config.export()  # used when saving
{'host': '${app_config::db_host}', 'port': '${app_config::expose}'}
```

You do not have to pass a variable name to `@include`, though. `@include` lets you overwrite the main interpolation namespace
or one with a separate name (here: `app_config`) with configuration models, dictionaries and their factories.

## Supported file formats

_configzen_ uses [anyconfig](https://pypi.org/project/anyconfig/) to serialize and deserialize data and does not operate on any protocol-specific entities.
As an example result, comments in your configuration files are lost on save[^1], but you can exchange file formats without any hassle.

The following table shows the supported file formats, their requirements, file extensions, and the backend libraries used to accomplish this goal.

| File Format                                                                         | To use, install:              | Recognized File Extension(s) | Backend Library                                                                                         |
|-------------------------------------------------------------------------------------|-------------------------------|------------------------------|---------------------------------------------------------------------------------------------------------|
| [JSON](https://en.wikipedia.org/wiki/JSON)                                          | -                             | `json`                       | [json](https://docs.python.org/3/library/json.html) (standard library)                                  |
| [INI](https://en.wikipedia.org/wiki/INI_file)                                       | -                             | `ini`, `cfg`, `conf`         | [configparser](https://docs.python.org/3/library/configparser.html) (standard library)                  |
| [TOML](https://en.wikipedia.org/wiki/TOML)                                          | -                             | `toml`                       | [toml](https://pypi.python.org/pypi/toml)                                                               |
| [YAML](https://yaml.org)                                                            | -                             | `yaml`, `yml`                | [pyyaml](https://pypi.python.org/pypi/PyYAML) / [ruamel.yml](https://pypi.python.org/pypi/ruamel.yml) |
| [XML](https://en.wikipedia.org/wiki/XML)                                            | -                             | `xml`                        | [xml](https://docs.python.org/3/library/xml.html) (standard library)                                    |
| [BSON](https://en.wikipedia.org/wiki/BSON)                                          | `anyconfig-bson-backend`      | `bson`                       | [bson](https://pypi.org/project/bson/)                                                                  |
| [CBOR](https://cbor.io/) ([RFC 8949](https://www.rfc-editor.org/rfc/rfc8949))       | `anyconfig-cbor2-backend`     | `cbor`, `cbor2`              | [cbor2](https://pypi.org/project/cbor2/)                                                                |
| CBOR (deprecated, [RFC 7049](https://www.rfc-editor.org/rfc/rfc7049))               | `anyconfig-cbor-backend`      | `cbor`                       | [cbor](https://pypi.org/project/cbor/)                                                                  |
| properties                                                                          | -                             | `properties`                 | (native)                                                                                                |
| shellvars                                                                           | -                             | `shellvars`                  | (native)                                                                                                |

[//]: # (| [ConfigObj]&#40;https://configobj.readthedocs.io/en/latest/configobj.html#introduction&#41; | `anyconfig-configobj-backend` | `configobj`                  | [configobj]&#40;https://pypi.org/project/configobj/&#41;                                                        |)
[//]: # (| [Amazon Ion]&#40;https://en.wikipedia.org/wiki/Ion_&#40;serialization_format&#41;&#41;              | `anyconfig-ion-backend`       | `ion`                        | [ion]&#40;https://pypi.org/project/amazon.ion/&#41;                                                             |)
[//]: # (| [MessagePack]&#40;https://en.wikipedia.org/wiki/MessagePack&#41;                            | `anyconfig-msgpack-backend`   | `msgpack`, `mpk`             | [msgpack]&#40;https://pypi.org/project/msgpack/&#41;                                                            |)

If your file extension is not recognized, you can register your own file extension by calling `ConfigAgent.register_file_extension(file_extension, parser_name)`.

If your favorite backend library is not supported, please let me know by reporting it as an issue.
Using custom backends is to be supported in the future.

[^1]: A suggested alternative for comments is to use the `description` parameter in your configuration models' fields: `ConfigField(description=...)`.
The provided field descriptions are included in JSON schemas generated by the default implementation of the `ConfigModel.schema()` method.

## Setup

In order to use _configzen_ in your project, install it with your package manager, for example `pip`:

```bash
pip install configzen
```

If you are willing to contribute to the project, which is awesome, simply clone the repository and install its
dependencies with [poetry](https://python-poetry.org/):

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

