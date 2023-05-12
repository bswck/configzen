from __future__ import annotations

import contextlib
import importlib
from collections.abc import ByteString
from typing import Any, ClassVar, TypeVar, Generic

__all__ = (
    "Engine",
    "get_engine_class",
)

import pydantic


ModelT = TypeVar("ModelT", bound=pydantic.BaseModel)


class Engine(Generic[ModelT]):
    config_class: type[ModelT]
    name: ClassVar[str]
    registry: dict[str, type[Engine]] = {}

    def __init__(self, **kwargs: Any) -> None:
        self.engine_options = kwargs

    def load(
        self: Engine[ModelT],
        *,
        model_class: type[ModelT],
        blob: str | ByteString | None,
    ) -> ModelT:
        """Load a config from a blob.

        Parameters
        ----------
        model_class : type[ModelT]
            The model class to load into.

        blob : str | ByteString | None
            The blob to load.
        """
        raise NotImplementedError

    def dump_object(self, obj: object) -> str | ByteString:
        raise NotImplementedError

    def dump(
        self: Engine[ModelT],
        model: ModelT,
    ) -> str | ByteString:
        """Dump a config to a blob.

        Parameters
        ----------
        model :
            The config to dump.

        Returns
        -------
        str | ByteString
            The dumped config blob.
        """
        return self.dump_object(model)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.name = name = cls.name.casefold()
        Engine.registry[name] = cls


def get_engine_class(engine_name: str) -> type[Engine]:
    """Get the engine class for the given engine name.

    Parameters
    ----------
    engine_name : str
        The name of the engine.

    Returns
    -------
    type[Engine]
        The engine class.

    Raises
    ------
    KeyError
        If the engine is not found.
    """
    engine_name = engine_name.casefold()
    if engine_name not in Engine.registry:
        with contextlib.suppress(ImportError):
            importlib.import_module(f"configzen.engines.{engine_name}_engine")

    return Engine.registry[engine_name]
