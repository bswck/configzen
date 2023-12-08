
# configzen [![Package version](https://img.shields.io/pypi/v/configzen?label=PyPI)](https://pypi.org/project/configzen/) [![Supported Python versions](https://img.shields.io/pypi/pyversions/configzen.svg?logo=python&label=Python)](https://pypi.org/project/configzen/)
[![Tests](https://github.com/bswck/configzen/actions/workflows/test.yml/badge.svg)](https://github.com/bswck/configzen/actions/workflows/test.yml)
[![Coverage](https://coverage-badge.samuelcolvin.workers.dev/bswck/configzen.svg)](https://coverage-badge.samuelcolvin.workers.dev/redirect/bswck/configzen)
[![Poetry](https://img.shields.io/endpoint?url=https://python-poetry.org/badge/v0.json)](https://python-poetry.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Code style](https://img.shields.io/badge/code%20style-black-000000.svg?label=Code%20style)](https://github.com/psf/black)
[![License](https://img.shields.io/github/license/bswck/configzen.svg?label=License)](https://github.com/bswck/configzen/blob/HEAD/LICENSE)
[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)

An advanced configuration library for Python.

> [!Warning]
> _configzen_ is currently under huge refactoring to work with pydantic v2.
> The API is subject to change.

_configzen_ – easily create and maintain complex, statically-typed configurations with validation in Python.

It's important to keep your configuration safe and consistent. Give a shot to _configzen_ 🚀

⭐ Supports **Python 3.8 or above**,<br>
⭐ Is **fully typed**,<br>
⭐ Supports **YAML, JSON, TOML, INI, XML, ConfigObj, BSON, CBOR, Amazon Ion, properties** and **shellvars**,<br>
⭐ Supports **reading and writing configuration files**, fully or partially, with the ability to preserve the original file structure (without comments[^1]),<br>
⭐ Supports **configuration preprocessing** (extending, including and copying configuration files without the need to change the code),<br>
⭐ Supports **variable interpolation** (runtime value substitution),<br>
⭐ Supports **modular configuration with type validation** (wrapping runtime Python modules in-place and outside them),<br>
⭐ Supports **synchronous and asynchronous file operations**,<br>
⭐ Supports loading configuration from **environment variables and secret files**.<br>

While being built on top of [pydantic](https://docs.pydantic.dev/1.10/), _configzen_ inherits most of its features, including
[data validation](https://docs.pydantic.dev/1.10/usage/validators/), [schema generation](https://docs.pydantic.dev/1.10/usage/schema/),
[custom data types](https://docs.pydantic.dev/1.10/usage/types/#arbitrary-types-allowed), good integration with [Rich](https://docs.pydantic.dev/1.10/usage/rich/), and more.

Learn more below.

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

from configzen import ConfigField, ConfigMeta, ConfigModel


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
instead of overwriting it entirely._

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
| ---------- | ------------------------------------ | ------------------------------------- |
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


### Modular configuration

#### Wrapping modules in-place

You can wrap modules in-place with configuration models:


1) Without writing a model class:

```python
# config.py
from configzen import ConfigModule

# Annotate config fields
HOST: str = "localhost"
PORT: int = 8000

ConfigModule.wrap_this_module()
```

2) With a model class:

```python
# config.py
from configzen import ConfigModel

# Annotations are optional
HOST = "localhost"
PORT = 8000

class AppConfig(ConfigModel):
    HOST: str
    PORT: int

AppConfig.wrap_this_module()
```

Now values `HOST` and `PORT` will be validated as `str` and `int` data types, respectively:

```python
>>> import config  # <configuration module 'config' from 'config.py'>
>>> config.HOST
'localhost'
>>> config.PORT
8000
>>> config.PORT = "8000"
>>> config.PORT
8000
>>> config.PORT = "abc"
Traceback (most recent call last):
  ...
```

#### Wrapping interchangeable modules

You can wrap modules outside them with configuration models:

```python
# setup.py
from configzen import ConfigModel

class AppConfig(ConfigModel):
    HOST: str = "localhost"
    PORT: int = 8000

config_model = AppConfig.wrap_module("config")
```

```py
# config.py
HOST: str = "0.0.0.0"
PORT: int = 443
```

```python
>>> from setup import config_model
>>> config_model.HOST
'0.0.0.0'
>>> config_model.PORT
443
>>> config_model.PORT = "8000"
>>> config_model.PORT
8000
>>> import config
>>> config.HOST
'0.0.0.0'
>>> config.PORT
8000
```

## Supported file formats

_configzen_ uses [anyconfig](https://pypi.org/project/anyconfig/) to serialize and deserialize data and does not operate on any protocol-specific entities.
As an example result, comments in your configuration files are lost on save[^1], but you can exchange file formats without any hassle.

The following table shows the supported file formats, their requirements, file extensions, and the backend libraries used to accomplish this goal.

| File Format                                                                         | To use, install:              | Recognized File Extension(s) | Backend Library                                                                                       |
| ----------------------------------------------------------------------------------- | ----------------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------------- |
| [JSON](https://en.wikipedia.org/wiki/JSON)                                          | -                             | `json`                       | [json](https://docs.python.org/3/library/json.html) (standard library)                                |
| [INI](https://en.wikipedia.org/wiki/INI_file)                                       | -                             | `ini`, `cfg`, `conf`         | [configparser](https://docs.python.org/3/library/configparser.html) (standard library)                |
| [TOML](https://en.wikipedia.org/wiki/TOML)                                          | -                             | `toml`                       | [toml](https://pypi.python.org/pypi/toml)                                                             |
| [YAML](https://yaml.org)                                                            | -                             | `yaml`, `yml`                | [pyyaml](https://pypi.python.org/pypi/PyYAML) / [ruamel.yaml](https://pypi.python.org/pypi/ruamel.yaml) |
| [XML](https://en.wikipedia.org/wiki/XML)                                            | -                             | `xml`                        | [xml](https://docs.python.org/3/library/xml.html) (standard library)                                  |
| [ConfigObj](https://configobj.readthedocs.io/en/latest/configobj.html#introduction) | `anyconfig-configobj-backend` | `configobj`                  | [configobj](https://pypi.org/project/configobj/)                                              |
| [BSON](https://en.wikipedia.org/wiki/BSON)                                          | `anyconfig-bson-backend`      | `bson`                       | [bson](https://pypi.org/project/bson/)                                                                |
| [CBOR](https://cbor.io/) ([RFC 8949](https://www.rfc-editor.org/rfc/rfc8949))       | `anyconfig-cbor2-backend`     | `cbor`, `cbor2`              | [cbor2](https://pypi.org/project/cbor2/)                                                              |
| [Amazon Ion](https://en.wikipedia.org/wiki/Ion_(serialization_format))              | `anyconfig-ion-backend`       | `ion`                        | [ion](https://pypi.org/project/amazon.ion/)                                                           |
| CBOR (deprecated, [RFC 7049](https://www.rfc-editor.org/rfc/rfc7049))               | `anyconfig-cbor-backend`      | `cbor`                       | [cbor](https://pypi.org/project/cbor/)                                                                |
| properties                                                                          | -                             | `properties`                 | (native)                                                                                              |
| shellvars                                                                           | -                             | `shellvars`                  | (native)                                                                                              |
<!-- Add msgpack when it works -->
If your file extension is not recognized, you can register your own file extension by calling `ConfigAgent.register_file_extension(file_extension, parser_name)`.

If your favorite backend library is not supported, please let me know by reporting it as an issue.
Using custom backends is to be supported in the future.

[^1]: A suggested alternative for comments is to use the `description` parameter in your configuration models' fields: `ConfigField(description=...)`.
The provided field descriptions are included in JSON schemas generated by the default implementation of the `ConfigModel.schema()` method.


# Installation
If you want to…



## …use this tool in your project 💻
You might simply install it with pip:

```shell
pip install configzen
```

If you use [Poetry](https://python-poetry.org/), then run:

```shell
poetry add configzen
```

## …contribute to [configzen](https://github.com/bswck/configzen) 🚀

<!--
This section was generated from bswck/skeleton@e4343ae.
Instead of changing this particular file, you might want to alter the template:
https://github.com/bswck/skeleton/tree/e4343ae/fragments/guide.md
-->

> [!Note]
> If you use Windows, it is highly recommended to complete the installation in the way presented below through [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install).



1.  Fork the [configzen repository](https://github.com/bswck/configzen) on GitHub.

1.  [Install Poetry](https://python-poetry.org/docs/#installation).<br/>
    Poetry is an amazing tool for managing dependencies & virtual environments, building packages and publishing them.
    You might use [pipx](https://github.com/pypa/pipx#readme) to install it globally (recommended):

    ```shell
    pipx install poetry
    ```

    <sub>If you encounter any problems, refer to [the official documentation](https://python-poetry.org/docs/#installation) for the most up-to-date installation instructions.</sub>

    Be sure to have Python 3.8 installed—if you use [pyenv](https://github.com/pyenv/pyenv#readme), simply run:

    ```shell
    pyenv install 3.8
    ```

1.  Clone your fork locally and install dependencies.

    ```shell
    git clone https://github.com/your-username/configzen path/to/configzen
    cd path/to/configzen
    poetry env use $(cat .python-version)
    poetry install
    ```

    Next up, simply activate the virtual environment and install pre-commit hooks:

    ```shell
    poetry shell
    pre-commit install --hook-type pre-commit --hook-type pre-push
    ```

For more information on how to contribute, check out [CONTRIBUTING.md](https://github.com/bswck/configzen/blob/HEAD/CONTRIBUTING.md).<br/>
Always happy to accept contributions! ❤️


# Legal info
© Copyright by Bartosz Sławecki ([@bswck](https://github.com/bswck)).
<br />This software is licensed under the terms of [MIT License](https://github.com/bswck/configzen/blob/HEAD/LICENSE).
