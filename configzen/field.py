from typing import Any

from pydantic.fields import Field, Undefined

__all__ = ("ConfigField",)


# noinspection PyPep8Naming
def ConfigField(default: Any = Undefined, **kwargs: Any) -> Any:  # noqa: N802
    # Since configzen involves BaseSettings implicitly,
    # this would be very convenient to have.
    alias = kwargs.get("alias")
    env = kwargs.get("env")
    if alias is not None and env is None:
        kwargs["env"] = alias
    return Field(default, **kwargs)
