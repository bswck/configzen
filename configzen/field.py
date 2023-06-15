from typing import Any, cast

from pydantic.fields import Field, FieldInfo, Undefined

__all__ = ("ConfigField",)


# noinspection PyPep8Naming
def ConfigField(  # noqa: N802
    default: Any = Undefined,
    **kwargs: Any
) -> FieldInfo:
    # Since configzen involves BaseSettings implicitly,
    # this would be very convenient to have.
    alias = kwargs.get("alias")
    env = kwargs.get("env")
    if alias is not None and env is None:
        kwargs["env"] = alias
    return cast(FieldInfo, Field(default, **kwargs))
