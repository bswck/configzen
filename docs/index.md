
# <div align="center">configzen<br>[![skeleton](https://img.shields.io/badge/0.0.2rc–245–g8c04714-skeleton?label=%F0%9F%92%80%20skeleton-ci/skeleton-python&labelColor=black&color=grey&link=https%3A//github.com/skeleton-ci/skeleton-python)](https://github.com/skeleton-ci/skeleton-python/tree/0.0.2rc-245-g8c04714) [![Supported Python versions](https://img.shields.io/pypi/pyversions/configzen.svg?logo=python&label=Python)](https://pypi.org/project/configzen/) [![Package version](https://img.shields.io/pypi/v/configzen?label=PyPI)](https://pypi.org/project/configzen/)</div>

[![Tests](https://github.com/bswck/configzen/actions/workflows/test.yml/badge.svg)](https://github.com/bswck/configzen/actions/workflows/test.yml)
[![Coverage](https://coverage-badge.samuelcolvin.workers.dev/bswck/configzen.svg)](https://coverage-badge.samuelcolvin.workers.dev/redirect/bswck/configzen)
[![Lifted?](https://tidelift.com/badges/package/pypi/configzen)](https://tidelift.com/subscription/pkg/pypi-configzen?utm_source=pypi-configzen&utm_medium=readme)

Manage configuration with pydantic.


# For Enterprise

| [![Tidelift](https://nedbatchelder.com/pix/Tidelift_Logo_small.png)](https://tidelift.com/subscription/pkg/pypi-configzen?utm_source=pypi-configzenutm_medium=referral&utm_campaign=readme) | [Available as part of the Tidelift Subscription.](https://tidelift.com/subscription/pkg/pypi-configzen?utm_source=pypi-configzen&&utm_medium=referral&utm_campaign=readme)<br>This project and the maintainers of thousands of other packages are working with Tidelift to deliver one enterprise subscription that covers all of the open source you use. [Learn more here](https://tidelift.com/subscription/pkg/pypi-configzen?utm_source=pypi-configzen&utm_medium=referral&utm_campaign=readthedocs). |
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
https://github.com/skeleton-ci/skeleton-python/tree/0.0.2rc-245-g8c04714/fragments/readme.md
-->
!!! Note
    If you use Windows, it is highly recommended to complete the installation in the way presented below through [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install).
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
