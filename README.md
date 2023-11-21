# configzen [![Package version](https://img.shields.io/pypi/v/configzen?label=PyPI)](https://pypi.org/project/configzen) [![Supported Python versions](https://img.shields.io/pypi/pyversions/configzen.svg?logo=python&label=Python)](https://pypi.org/project/configzen)
[![Tests](https://github.com/bswck/configzen/actions/workflows/test.yml/badge.svg)](https://github.com/bswck/configzen/actions/workflows/test.yml)
[![Coverage](https://coverage-badge.samuelcolvin.workers.dev/bswck/configzen.svg)](https://coverage-badge.samuelcolvin.workers.dev/redirect/bswck/configzen)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Code style](https://img.shields.io/badge/code%20style-black-000000.svg?label=Code%20style)](https://github.com/psf/black)
[![License](https://img.shields.io/github/license/bswck/configzen.svg?label=License)](https://github.com/bswck/configzen/blob/pydantic-v2/LICENSE)

Easily create and maintain complex, statically-typed configurations with validation in Python.


# Installation
If you want to…


## …use this tool in your project 💻
You might simply install it with pip:
```bash
pip install configzen
```

If you use [Poetry](https://python-poetry.org/), then run:
```bash
poetry add configzen
```

## …contribute to [configzen](https://github.com/bswck/configzen) 🚀

Happy to accept contributions!

> [!Note]
> If you use Windows, it is highly recommended to complete the installation in the way presented below through [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install).

First, [install Poetry](https://python-poetry.org/docs/#installation).<br/>
Poetry is an amazing tool for managing dependencies & virtual environments, building packages and publishing them.

```bash
pipx install poetry
```
<sub>If you encounter any problems, refer to [the official documentation](https://python-poetry.org/docs/#installation) for the most up-to-date installation instructions.</sub>

Be sure to have Python 3.8 installed—if you use [pyenv](https://github.com/pyenv/pyenv#readme), simply run:
```bash
pyenv install 3.8
```

Then, run:
```bash
git clone https://github.com/bswck/configzen path/to/configzen
cd path/to/configzen
poetry env use $(cat .python-version)
poetry install
poetry shell
pre-commit install --hook-type pre-commit --hook-type pre-push
```

# Legal info
© Copyright by Bartosz Sławecki ([@bswck](https://github.com/bswck)).<br />This software is licensed under the [MIT License](https://github.com/bswck/configzen/blob/main/LICENSE).

