"""Runtime modules with attribute type validation."""

from __future__ import annotations

import inspect
import sys
import types
from typing import Any, Generic, cast

from configzen.typedefs import ConfigObject

__all__ = ("ModuleProxy",)


def _is_dunder(name: str) -> bool:
    return (
        len(name) > 4  # noqa: PLR2004
        # and name.isidentifier() -- used internally, so we don't need to check
        and name.startswith("__")
        and name[2] != "_"
        and name.endswith("__")
    )


class ModuleProxy(types.ModuleType, Generic[ConfigObject]):
    """
    Proxy object that extends a runtime module with type validation.

    Triggered via a config instance (initialization and assignment).

    Parameters
    ----------
    name
        The name of the module.
    config
        The configuration model to use for type validation.
    module_namespace
        The module namespace to wrap.

    """

    __config__: ConfigObject
    __locals__: dict[str, Any]

    def __init__(
        self,
        name: str,
        config: ConfigObject,
        module_namespace: dict[str, Any] | None = None,
        doc: str | None = None,
    ) -> None:
        object.__setattr__(self, "__config__", config)
        object.__setattr__(self, "__locals__", module_namespace or {})
        object.__setattr__(config, "__wrapped_module__", self)

        super().__init__(name=name, doc=doc)

        parts = name.split(".")
        if len(parts) > 1:
            # Set the proxy module as an attribute of its parent.
            parent = sys.modules[".".join(parts[:-1])]
            setattr(parent, parts[-1], self)

        # Make reusable.
        sys.modules[name] = self

    def __getattribute__(self, name: str) -> Any:
        """Get an attribute of the underlying model."""
        if _is_dunder(name):
            return object.__getattribute__(self, name)

        config = self.__config__
        try:
            return getattr(config, name)
        except AttributeError:
            try:
                return self.__locals__[name]
            except KeyError:
                return object.__getattribute__(self, name)

    def __setattr__(self, key: str, value: Any) -> None:
        """Set an attribute on the underlying model."""
        config = self.get_config()
        if not _is_dunder(key) and key in config.model_fields:
            setattr(config, key, value)
        self.__locals__[key] = value

    def __repr__(self) -> str:
        """
        Get the string representation of the module proxy.

        Inform the user that this is a configuration module.
        """
        return super().__repr__().replace("module", "configuration module", 1)

    def get_config(self) -> ConfigObject:
        """Get the configuration model."""
        return self.__config__

    @classmethod
    def wrap_module(
        cls,
        module_name: str,
        config_class: type[ConfigObject] | None = None,
        namespace: dict[str, Any] | None = None,
        /,
        **values: Any,
    ) -> ModuleProxy[ConfigObject]:
        """
        Wrap a module to ensure type validation.

        Every attribute of the wrapped module that is also a field of the config will be
        validated against it. The module will be extended with the config's attributes.
        Assignments on the module's attributes will be propagated to the configuration
        instance. It could be said that the module becomes a proxy for the configuration
        once wrapped.

        Parameters
        ----------
        module_name
            The name of the module to wrap.
        config_class
            The config class to use for type validation.
        namespace
            The namespace of the module to wrap. If not provided, it will be
            retrieved from `sys.modules`.
        values
            Values used to initialize the config.

        Returns
        -------
        The wrapped module.

        """
        from configzen.config import BaseConfig

        if namespace is None:
            module_namespace = vars(sys.modules[module_name])
        else:
            module_namespace = namespace

        if config_class is None:

            class ConfigModule(BaseConfig):
                __module__ = module_name
                __annotations__ = module_namespace["__annotations__"]
                for key in __annotations__:
                    locals()[key] = module_namespace[key]

            config_class = cast("type[ConfigObject]", ConfigModule)

        module_values = {}
        field_names = frozenset(
            field_info.validation_alias
            or field_info.alias
            or field_info.title
            or field_name
            for field_name, field_info in config_class.model_fields.items()
        )
        for key, value in module_namespace.items():
            if key in field_names:
                module_values[key] = value
        config = config_class.model_validate({**module_values, **values})

        return cls(
            config=config,
            module_namespace=module_namespace,
            name=module_namespace.get("__name__") or module_name,
            doc=module_namespace.get("__doc__"),
        )

    @classmethod
    def wrap_this_module(
        cls,
        config_class: type[ConfigObject] | None = None,
        /,
        **values: Any,
    ) -> ModuleProxy[ConfigObject]:
        """
        Wrap the module calling this function.

        For more information on wrapping modules, see `ModuleProxy.wrap_module()`.

        Parameters
        ----------
        config_class
            The config class to use for type validation.
        values
            Values used to initialize the config.

        """
        current_frame = inspect.currentframe()
        if current_frame is None:
            msg = "Could not get the current frame"
            raise RuntimeError(msg)
        frame_back = current_frame.f_back
        if frame_back is None:
            msg = "Could not get the frame back"
            raise RuntimeError(msg)
        return cls.wrap_module(
            {**frame_back.f_globals, **frame_back.f_locals}["__name__"],
            config_class,
            {**frame_back.f_locals, **values},
        )
