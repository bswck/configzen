from __future__ import annotations

import inspect
import sys
import types
from typing import Any, Generic, cast

from configzen.typedefs import Configuration

__all__ = ("ModuleProxy",)


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


class ModuleProxy(types.ModuleType, Generic[Configuration]):
    """
    Proxy object that extends runtime module with type validation triggered
    via a config instance (initialization and assignment).

    Parameters
    ----------
    name
        The name of the module.
    config
        The configuration model to use for type validation.
    module_namespace
        The module namespace to wrap.
    """

    __configuration__: Configuration
    __locals__: dict[str, Any]

    def __init__(
        self,
        name: str,
        config: Configuration,
        module_namespace: dict[str, Any] | None = None,
        doc: str | None = None,
    ) -> None:
        object.__setattr__(self, "__configuration__", config)
        object.__setattr__(self, "__locals__", module_namespace)
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
        if _is_dunder(name):
            return object.__getattribute__(self, name)

        config = self.__configuration__
        try:
            return getattr(config, name)
        except AttributeError:
            try:
                return self.__locals__[name]
            except KeyError:
                return object.__getattribute__(self, name)

    def __setattr__(self, key: str, value: Any) -> None:
        config = self.get_config()
        if not _is_dunder(key) and key in config.model_fields:
            setattr(config, key, value)
        self.__locals__[key] = value

    def __repr__(self) -> str:
        return super().__repr__().replace("module", "configuration module", 1)

    def get_config(self) -> Configuration:
        return self.__configuration__

    @classmethod
    def wrap_module(
        cls,
        module_name: str,
        configuration_class: type[Configuration] | None = None,
        namespace: dict[str, Any] | None = None,
        /,
        **values: Any,
    ) -> ModuleProxy[Configuration]:
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
        configuration_class
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
        from configzen.configuration import BaseConfiguration

        if namespace is None:
            module_namespace = vars(sys.modules[module_name])
        else:
            module_namespace = namespace

        if configuration_class is None:

            class ConfigurationModule(BaseConfiguration):
                __module__ = module_name
                __annotations__ = module_namespace["__annotations__"]
                __temp__ = locals()
                for key in __annotations__:
                    __temp__[key] = module_namespace[key]
                del __temp__

            configuration_class = cast("type[Configuration]", ConfigurationModule)

        module_values = {}
        for key, value in module_namespace.items():
            if key in {
                field.validation_alias
                for field in configuration_class.model_fields.values()
            }:
                module_values[key] = value

        return cls(
            config=configuration_class.model_validate({**module_values, **values}),
            module_namespace=module_namespace,
            name=module_namespace["__name__"],
            doc=module_namespace["__doc__"],
        )

    @classmethod
    def wrap_this_module(
        cls,
        configuration_class: type[Configuration] | None = None,
        /,
        **values: Any,
    ) -> ModuleProxy[Configuration]:
        """
        Wrap the module calling this function.
        For more information on wrapping modules, see `ModuleProxy.wrap_module()`.

        Parameters
        ----------
        configuration_class
            The config class to use for type validation.
        values
            Values used to initialize the config.
        """
        current_frame = inspect.currentframe()
        assert current_frame is not None
        frame_back = current_frame.f_back
        assert frame_back is not None
        return cls.wrap_module(
            frame_back.f_globals["__name__"],
            configuration_class,
            **values,
        )
