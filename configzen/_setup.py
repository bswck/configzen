"""
Convenience hooks for quicker development with _configzen_.

For advanced use cases, you can prevent this module from executing
by setting the environment variable ``CONFIGZEN_SETUP`` to ``0``.
"""

from __future__ import annotations

import ast
import collections.abc
import pathlib
from typing import TYPE_CHECKING, Any

from pydantic.json import ENCODERS_BY_TYPE

from configzen.config import export_hook, field_hook, ConfigModel

if TYPE_CHECKING:
    from configzen.typedefs import ConfigModelT

for obj_type, obj_encoder in ENCODERS_BY_TYPE.items():
    export_hook.register(obj_type, obj_encoder)


@export_hook.register(pathlib.WindowsPath)
@export_hook.register(pathlib.PureWindowsPath)
def _export_windows_path(obj: pathlib.WindowsPath | pathlib.PureWindowsPath) -> str:
    """
    This hook makes non-absolute paths in configurations cross-platform.

    Parameters
    ----------
    obj
        The path to convert.

    Returns
    -------
    The converted path.
    """
    return obj.as_posix()


@export_hook.register(list)
def _export_list(obj: list[Any]) -> list[Any]:
    return [export_hook(item) for item in obj]


@export_hook.register(collections.abc.Mapping)
def _export_mapping(obj: collections.abc.Mapping[Any, Any]) -> dict[Any, Any]:
    return {k: export_hook(v) for k, v in obj.items()}


@field_hook.register(dict)
@field_hook.register(list)
def _eval_literals(cls: type[Any], value: Any) -> Any:
    """
    Load a dict/list using literal evaluation.
    Solves nested dictionaries problem in INI files.

    Parameters
    ----------
    cls
        The type to load the value into.

    value
        The value to load.

    Returns
    -------
    The loaded value.
    """
    if isinstance(value, str):
        try:
            data = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            # There might be some following validator, let it be like that
            return value
        else:
            return cls(data)
    return value


@field_hook.register(ConfigModel)
def _eval_model(cls: type[ConfigModelT], value: Any) -> ConfigModelT | Any:
    """
    Load a model using dict literal evaluation.
    Solves nested dictionaries problem in INI files.

    Parameters
    ----------
    cls
        The type to load the value into.

    value
        The value to load.

    Returns
    -------
    The loaded value.
    """
    data = value
    if isinstance(value, str):
        try:
            data = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            # Note: Strings resembling models is probably not intended
            # to be used with automatic pickle/JSON parsing.
            # return cls.parse_raw(value)
            return data
        else:
            return cls.parse_obj(data)
    return data
