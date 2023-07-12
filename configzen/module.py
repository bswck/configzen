from __future__ import annotations

import inspect
import sys
import types
from typing import TYPE_CHECKING, Any, Generic, cast

from configzen.typedefs import ConfigModelT

if TYPE_CHECKING:
    from configzen import ConfigModel

__all__ = ("ConfigModule",)

MODULE: str = "__wrapped_module__"


class ConfigModule(types.ModuleType, Generic[ConfigModelT]):
    __model: ConfigModelT
    __vars: dict[str, Any]

    def __init__(
        self,
        name: str,
        model: ConfigModelT,
        module_vars: dict[str, Any] | None = None,
        doc: str | None = None,
    ) -> None:
        object.__setattr__(self, f"_{ConfigModule.__name__}__model", model)
        object.__setattr__(self, f"_{ConfigModule.__name__}__vars", module_vars)
        object.__setattr__(model, MODULE, self)
        super().__init__(name=name, doc=doc)
        parts = name.split(".")

        if len(parts) > 1:
            # Set the proxy module as an attribute of its parent.
            parent = sys.modules[".".join(parts[:-1])]
            setattr(parent, parts[-1], self)

        # Make reusable.
        sys.modules[name] = self

    def __getattribute__(self, name: str) -> Any:
        if name.startswith(f"_{ConfigModule.__name__}__"):
            return object.__getattribute__(self, name)

        model = self.__model
        try:
            return getattr(model, name)
        except AttributeError:
            try:
                return self.__vars[name]
            except KeyError:
                return object.__getattribute__(self, name)

    def __setattr__(self, key: str, value: Any) -> None:
        model = self.get_model()
        if (
            not key.startswith(f"_{ConfigModule.__name__}__")
            and key in model.__fields__
        ):
            setattr(model, key, value)
        self.__vars[key] = value

    def __repr__(self) -> str:
        return super().__repr__().replace("module", "configuration module", 1)

    def get_model(self) -> ConfigModelT:
        return self.__model

    @classmethod
    def wrap_module(
        cls,
        module_name: str,
        model_class: type[ConfigModelT] | None = None,
        module_vars: dict[str, Any] | None = None,
        /,
        **values: Any,
    ) -> ConfigModule[ConfigModelT]:
        """Wrap a Python module to interact with a config model."""
        from configzen.model import ConfigModel

        if module_vars is None:
            module_vars = vars(sys.modules[module_name])

        if model_class is None:

            class ModuleConfigModel(ConfigModel):
                __module__ = module_name
                __annotations__ = module_vars["__annotations__"]  # type: ignore[index]
                __ns = locals()
                for key in __annotations__:
                    __ns[key] = module_vars[key]  # type: ignore[index]
                del __ns

            model_class = cast(type[ConfigModelT], ModuleConfigModel)

        config_module = cls(
            model=cast(ConfigModelT, model_class).parse_obj(values),
            module_vars=module_vars,
            name=module_vars["__name__"],
            doc=module_vars["__doc__"],
        )
        return config_module

    @classmethod
    def wrap_this_module(
        cls,
        model_class: type[ConfigModelT] | None = None,
        /,
        **values: Any,
    ) -> ConfigModule[ConfigModelT]:
        """Wrap the current module to interact with a config model."""
        current_frame = inspect.currentframe()
        assert current_frame is not None
        frame_back = current_frame.f_back
        assert frame_back is not None
        return cls.wrap_module(
            frame_back.f_globals["__name__"],
            model_class,
            **values,
        )
