# <div align="center">configzen<br>[![skeleton](https://img.shields.io/badge/0.0.2rc–245–g8c04714-skeleton?label=%F0%9F%92%80%20skeleton-ci/skeleton-python&labelColor=black&color=grey&link=https%3A//github.com/skeleton-ci/skeleton-python)](https://github.com/skeleton-ci/skeleton-python/tree/0.0.2rc-245-g8c04714) [![Supported Python versions](https://img.shields.io/pypi/pyversions/configzen.svg?logo=python&label=Python)](https://pypi.org/project/configzen/) [![Package version](https://img.shields.io/pypi/v/configzen?label=PyPI)](https://pypi.org/project/configzen/)</div>

[![Tests](https://github.com/bswck/configzen/actions/workflows/test.yml/badge.svg)](https://github.com/bswck/configzen/actions/workflows/test.yml)
[![Coverage](https://coverage-badge.samuelcolvin.workers.dev/bswck/configzen.svg)](https://coverage-badge.samuelcolvin.workers.dev/redirect/bswck/configzen)
[![Lifted?](https://tidelift.com/badges/package/pypi/configzen)](https://tidelift.com/subscription/pkg/pypi-configzen?utm_source=pypi-configzen&utm_medium=readme)

Manage configuration with pydantic.

> [!Warning]
> configzen is currently under huge refactoring to work with pydantic v2.
> The API is subject to change.

While being built on top of [pydantic](https://docs.pydantic.dev/2.6/), configzen inherits most of its features, including
[data validation](https://docs.pydantic.dev/2.6/concepts/models/#validation), [schema generation](https://docs.pydantic.dev/2.6/api/base_model/#pydantic.BaseModel.model_json_schema),
[custom data types](https://docs.pydantic.dev/2.6/api/config/#pydantic.config.ConfigDict.arbitrary_types_allowed), good integration with [Rich](https://docs.pydantic.dev/2.6/integrations/rich/), and more.

Learn more below.

## Features

### Managing Content

Having a YAML configuration file like this:

```yaml
# database.yml
host: 127.0.0.1
port: 5432
user: postgres
```

You can create a configzen configuration model for it like this:

```python
# model.py
from ipaddress import IPv4Address, IPv6Address

from configzen import BaseConfig, Field, ModelConfig


class DBConfig(BaseConfig):
    host: IPv4Address | IPv6Address
    port: int
    user: str
    password: str = Field(exclude=True)

    model_config = ModelConfig(
        config_source="database.yml",
        env_prefix="DB_",
    )


db_config = DBConfig.load()
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

There are additional features brought to you by configzen worth checking out, though.

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
>>> db_config.at(DBConfig.port).reload()
5432
# `DBConfig.port` is a LinkedRoute object that ensures `port` of `DBConfig` exists!
>>> db_config
DatabaseConfig(host=IPv4Address('0.0.0.0'), port=5432, user='postgres', password='password')
>>> db_config.at(DBConfig.host).reload()
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
>>> db_config.at(DBConfig.host).save()
40
>>> db_config.reload()
DatabaseConfig(host=IPv4Address('0.0.0.0'), port=5432, user='postgres', password='password')
```

or save the whole configuration:

```python
>>> db_config.save()
39
```


### Modular Configuration

#### Wrapping Modules In-Place

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

#### Wrapping Interchangeable Modules

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

## Supported File Formats

The following table shows the supported file formats, their requirements, file extensions, and the backend libraries used to accomplish this goal.

| File Format                                   | To use, install: | Recognized File Extension(s) | Backend Library                                                                                         |
| --------------------------------------------- | ---------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------- |
| [JSON](https://en.wikipedia.org/wiki/JSON)    | -                | `json`                       | [json](https://docs.python.org/3/library/json.html) (standard library)                                  |
| [TOML](https://en.wikipedia.org/wiki/TOML)    | -                | `toml`, `ini`, `cfg`, `conf` | [tomlkit](https://pypi.python.org/pypi/tomlkit)                                                               |
| [YAML](https://yaml.org)                      | -                | `yaml`, `yml`                | [ruamel.yaml](https://pypi.python.org/pypi/ruamel.yaml) |
| [Plist](https://en.wikipedia.org/wiki/XML)    | -                | `plist`                      | [plistlib](https://docs.python.org/3/library/plistlib.html) (standard library)                                    |

# For Enterprise

| [![Tidelift](https://nedbatchelder.com/pix/Tidelift_Logo_small.png)](https://tidelift.com/subscription/pkg/pypi-configzen?utm_source=pypi-configzenutm_medium=referral&utm_campaign=readme) | [Available as part of the Tidelift Subscription.](https://tidelift.com/subscription/pkg/pypi-configzen?utm_source=pypi-configzen&&utm_medium=referral&utm_campaign=readme)<br>This project and the maintainers of thousands of other packages are working with Tidelift to deliver one enterprise subscription that covers all of the open source you use. [Learn more here](https://tidelift.com/subscription/pkg/pypi-configzen?utm_source=pypi-configzen&utm_medium=referral&utm_campaign=github). |
| - | - |

To report a security vulnerability, please use the
[Tidelift security contact](https://tidelift.com/security).<br>
Tidelift will coordinate the fix and disclosure.

# Installation
You might simply install it with pip:

```shell
pip install configzen
```

If you use [Poetry](https://python-poetry.org/), then you might want to run:

```shell
poetry add configzen
```

## For Contributors
[![Poetry](https://img.shields.io/endpoint?url=https://python-poetry.org/badge/v0.json)](https://python-poetry.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
<!--
This section was generated from skeleton-ci/skeleton-python@0.0.2rc-245-g8c04714.
Instead of changing this particular file, you might want to alter the template:
https://github.com/skeleton-ci/skeleton-python/tree/0.0.2rc-245-g8c04714/project/README.md.jinja
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
    pre-commit install
    ```

For more information on how to contribute, check out [CONTRIBUTING.md](https://github.com/bswck/configzen/blob/HEAD/CONTRIBUTING.md).<br/>
Always happy to accept contributions! ❤️

# Legal Info
© Copyright by Bartosz Sławecki ([@bswck](https://github.com/bswck)).
<br />This software is licensed under the terms of [GPL-3.0 License](https://github.com/bswck/configzen/blob/HEAD/LICENSE).
