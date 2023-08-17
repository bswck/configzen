from __future__ import annotations

import asyncio
import copy
import dataclasses
import enum
import pathlib
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypedDict, TypeVar, cast

from anyconfig.utils import is_dict_like, is_list_like
from pydantic.fields import Undefined

from configzen.errors import ConfigPreprocessingError
from configzen.typedefs import ConfigModelT, ConfigRouteLike

if TYPE_CHECKING:
    from collections.abc import Callable

    from configzen.model import BaseContext, ConfigAgent

__all__ = (
    "DirectiveContext",
    "directive",
    "Processor",
)

DirectiveT = TypeVar("DirectiveT")

EXPORT: str = "__configzen_export__"
EXECUTES_DIRECTIVES: str = "__configzen_executes_directives__"
EXECUTES_DIRECTIVES_ASYNC: str = "__configzen_executes_directives_async__"


def directive(
    name: str | enum.Enum,
    *,
    asynchronous: bool | None = None,
) -> Callable[..., Any]:
    """
    Create a processor directive (a.k.a. preprocessing directive).

    Parameters
    ----------
    name
        The name of the directive.
    asynchronous
        Whether the decorated directive function is asynchronous.

    Returns
    -------
    The decorated function.
    """
    if isinstance(name, enum.Enum):
        name = name.value.casefold()

    def decorator(func: Any) -> Any:
        nonlocal asynchronous
        if asynchronous is None:
            asynchronous = asyncio.iscoroutinefunction(func)
        attr = EXECUTES_DIRECTIVES_ASYNC if asynchronous else EXECUTES_DIRECTIVES
        if not hasattr(func, attr):
            setattr(func, attr, set())
        getattr(func, attr).add(name)
        return func

    return decorator


@dataclasses.dataclass
class DirectiveContext:
    """
    Context for processor directives.

    Attributes
    ----------
    directive
        The directive.
    key
        The key of the directive.
    prefix
        The prefix of the directive.
    snippet
        The config snippet where this directive was invoked.
    container
        The dictionary that contains the :attr:`dict`.

    """

    directive: str
    key: str
    prefix: str
    snippet: dict[str, Any]
    container: dict[str, Any]


def parse_directive_call(
    prefix: str,
    directive_name: str,
) -> str:
    if directive_name.startswith(prefix):
        directive_name = directive_name[len(prefix) :].casefold()

        if not directive_name.isidentifier():
            msg = f"Invalid directive name: {directive_name}"
            raise ConfigPreprocessingError(msg)

    return directive_name


if TYPE_CHECKING:

    class ExportMetadata(TypedDict, Generic[ConfigModelT]):
        route: str | None
        context: BaseContext[ConfigModelT]
        key_order: list[str]
        preprocess: bool

else:

    class ExportMetadata(TypedDict):
        """
        Metadata for exporting.

        Attributes
        ----------
        route
            The route to import from.
        context
            The context attached to the import.
        """

        route: str | None
        context: BaseContext[ConfigModelT]
        key_order: list[str]
        preprocess: bool


class BaseProcessor(Generic[ConfigModelT]):
    """
    Processor that executes directives.

    Attributes
    ----------
    dict_config
        The dictionary config to parse and update.
    directive_prefix
        The prefix for directives.
    """

    _directive_handlers: dict[str, Any] = None  # type: ignore[assignment]
    _async_directive_handlers: dict[str, Any] = None  # type: ignore[assignment]
    directive_prefix: ClassVar[str]
    extension_prefix: ClassVar[str]

    def __init__(
        self,
        agent: ConfigAgent[ConfigModelT],
        dict_config: dict[str, Any],
    ) -> None:
        self.agent = agent
        self.dict_config = dict_config

    @classmethod
    def export(
        cls,
        state: Any,
        *,
        metadata: ExportMetadata[ConfigModelT] | None = None,
    ) -> None:
        """
        Export the state.

        Parameters
        ----------
        state
            The state to export.
        metadata
            The metadata of the substitution.
        """
        if is_dict_like(state):
            if metadata is None:
                from configzen.model import CONTEXT

                state.pop(CONTEXT, None)
                metadata = state.pop(EXPORT, None)
            if metadata:
                cls._export(state, metadata)
            else:
                cls.export(list(state.values()))
        elif is_list_like(state):
            for item in state:
                cls.export(item)

    @classmethod
    async def export_async(
        cls,
        state: Any,
        *,
        metadata: ExportMetadata[ConfigModelT] | None = None,
    ) -> None:
        """
        Export the state asynchronously.

        Parameters
        ----------
        state
            The state to export.
        metadata
            The metadata of the substitution.
        """
        if is_dict_like(state):
            if metadata is None:
                from configzen.model import CONTEXT

                state.pop(CONTEXT, None)
                metadata = state.pop(EXPORT, None)
            if metadata:
                await cls._export_async(state, metadata)
            else:
                await cls.export_async(list(state.values()))
        elif is_list_like(state):
            for item in state:
                await cls.export_async(item)

    @classmethod
    def _export(
        cls,
        state: Any,
        metadata: ExportMetadata[ConfigModelT],
    ) -> None:
        raise NotImplementedError

    @classmethod
    async def _export_async(
        cls,
        state: Any,
        metadata: ExportMetadata[ConfigModelT],
    ) -> None:
        raise NotImplementedError

    async def preprocess_async(self) -> dict[str, Any]:
        """
        Parse the dictionary config and return the parsed config dict.

        The parsed config dict is ready for instantiating the model.

        Returns
        -------
        The parsed config.
        """
        return cast("dict[str, Any]", await self._preprocess_async(self.dict_config))

    def preprocess(self) -> dict[str, Any]:
        """
        Parse the dictionary config and return the parsed config,
        ready for instantiating the model.

        Returns
        -------
        The parsed config.
        """
        return cast("dict[str, Any]", self._preprocess(self.dict_config))

    def _preprocess(self, container: Any) -> Any:
        if not is_dict_like(container):
            if is_list_like(container):
                return [self._preprocess(v) for v in container]
            return container

        result: dict[str, Any] = {}

        for key, value in sorted(
            cast("dict[str, Any]", container).items(),
            key=lambda item: item[0] == self.directive_prefix,
        ):
            if key.startswith(self.extension_prefix):
                actual_key = key.lstrip(self.extension_prefix)
                overridden = result.get(actual_key, {})
                if not is_dict_like(overridden):
                    msg = (
                        f"{self.extension_prefix} can be used only for overriding "
                        f"dictionary sections but item at {actual_key!r}"
                        "is not a dictionary"
                    )
                    raise ConfigPreprocessingError(msg)
                replacement = {**overridden, **value}
                result[actual_key] = self._preprocess(replacement)
            elif key.startswith(self.directive_prefix):
                directive_name = parse_directive_call(self.directive_prefix, key)
                context_container = container.copy()
                del context_container[key]
                context = DirectiveContext(
                    directive=directive_name,
                    key=key,
                    prefix=self.directive_prefix,
                    snippet=value,
                    container=context_container,
                )
                self._call_directive(context)
                new_container = self._preprocess(context.container)
                result.update(new_container)
            else:
                result[key] = self._preprocess(value)
        return result

    async def _preprocess_async(self, container: Any) -> Any:
        if not is_dict_like(container):
            if is_list_like(container):
                return [await self._preprocess_async(v) for v in container]
            return container

        result: dict[str, Any] = {}

        for key, value in sorted(
            cast("dict[str, Any]", container).items(),
            key=lambda item: item[0] == self.directive_prefix,
        ):
            if key.startswith(self.extension_prefix):
                actual_key = key.lstrip(self.extension_prefix)
                overridden = result.get(actual_key, {})
                if not is_dict_like(overridden):
                    msg = (
                        f"{self.extension_prefix} can be used only for overriding "
                        f"dictionary sections but item at {actual_key!r} "
                        "is not a dictionary"
                    )
                    raise ConfigPreprocessingError(msg)
                replacement = {**overridden, **value}
                result[actual_key] = await self._preprocess_async(replacement)
            elif key.startswith(self.directive_prefix):
                directive_name = parse_directive_call(self.directive_prefix, key)
                context_container = container.copy()
                del context_container[key]
                context = DirectiveContext(
                    directive=directive_name,
                    key=key,
                    prefix=self.directive_prefix,
                    snippet=value,
                    container=context_container,
                )
                await self._call_directive_async(context)
                new_container = await self._preprocess_async(context.container)
                result.update(new_container)
            else:
                result[key] = await self._preprocess_async(value)
        return result

    def _call_directive(self, context: DirectiveContext) -> None:
        handler = self._directive_handlers.get(context.directive)
        if handler is None:
            msg = f"unknown preprocessing directive: {context.directive!r}"
            raise ConfigPreprocessingError(msg)
        handler(self, context)

    async def _call_directive_async(self, context: DirectiveContext) -> None:
        handler = self._async_directive_handlers.get(context.directive)
        if handler is None:
            msg = f"unknown preprocessing directive: {context.directive!r}"
            raise ConfigPreprocessingError(msg)
        await handler(self, context)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls._directive_handlers is None:
            cls._directive_handlers = {}
        else:
            cls._directive_handlers = cls._directive_handlers.copy()
        if cls._async_directive_handlers is None:
            cls._async_directive_handlers = {}
        else:
            cls._async_directive_handlers = cls._async_directive_handlers.copy()
        for func in cls.__dict__.values():
            if hasattr(func, EXECUTES_DIRECTIVES):
                for directive_name in getattr(func, EXECUTES_DIRECTIVES):
                    cls._directive_handlers[directive_name] = func
            elif hasattr(func, EXECUTES_DIRECTIVES_ASYNC):
                for directive_name in getattr(func, EXECUTES_DIRECTIVES_ASYNC):
                    cls._async_directive_handlers[directive_name] = func

    @classmethod
    def register_directive(cls, name: str, func: Any) -> None:
        if cls._directive_handlers is None:
            cls._directive_handlers = {}
        cls._directive_handlers[name] = func

    @classmethod
    def directive(cls, directive_name: str) -> str:
        """
        Create a directive call.

        Parameters
        ----------
        directive_name
            The name of the directive.

        Returns
        -------
        The directive call.
        """
        if isinstance(directive_name, enum.Enum):
            directive_name = directive_name.value

        return cls.directive_prefix + directive_name


class Directives(str, enum.Enum):
    __slots__ = ()  # https://beta.ruff.rs/docs/rules/no-slots-in-str-subclass/
    EXTEND = "extend"
    INCLUDE = "include"
    COPY = "copy"


class Processor(BaseProcessor[ConfigModelT]):
    directive_prefix = "^"
    extension_prefix = "+"
    route_separator: ClassVar[str] = ":"

    @directive(Directives.EXTEND)
    def extend(self, ctx: DirectiveContext) -> None:
        """
        Extend a configuration with another configuration.
        Recursively preprocess the referenced configuration.
        Preserve information about the referenced configuration.

        Visual example
        --------------

        With `base.yaml` containing:
        ```yaml
        section:
          foo: 1
          bar: 2
        ```

        and `config.yaml` containing:

        ```yaml
        ^extend: base.yaml
        +section:
          foo: 3
        ```

        -> `load()` -> `save()` ->

        ```yaml
        ^extend: base.yaml
        +section:
          foo: 3
        ```
        """
        self._substitute(ctx, preprocess=True, preserve=True)

    @directive(Directives.INCLUDE)
    def include(self, ctx: DirectiveContext) -> None:
        """
        Include a configuration in another configuration.
        Recursively preprocess the referenced configuration.
        Do not preserve information about the referenced configuration.

        Visual example
        --------------
        With `biz.yaml` containing:

        ```yaml
        section:
          biz: 3
        ```

        and `base.yaml` containing:

        ```yaml
        ^extend: biz.yaml
        +section:
          foo: 1
          bar: 2
        ```

        and `config.yaml` containing:

        ```yaml
        ^include: base.yaml
        +section:
          foo: 3
        ```

        -> `load()` -> `save()` ->

        ```yaml
        ^extend: biz.yaml
        +section:
          bar: 2
          foo: 3
        ```
        """
        self._substitute(ctx, preprocess=True, preserve=False)

    @directive(Directives.COPY)
    def copy(self, ctx: DirectiveContext) -> None:
        """
        Copy a configuration and paste into another configuration.
        This is just a literal copy-paste.
        Do not preprocess the referenced configuration.
        Do not preserve information about the referenced configuration.

        Visual example
        --------------
        With `base.yaml` containing:

        ```yaml
        section:
          foo: 1
          bar: 2
        ```

        and `config.yaml` containing:

        ```yaml
        ^copy: base.yaml
        +section:
          foo: 3
        ```

        -> `load()` -> `save()` ->

        ```yaml
        section:
          foo: 3
          bar: 2
        ```
        """
        self._substitute(ctx, preprocess=False, preserve=False)

    @directive(Directives.EXTEND)
    async def extend_async(self, ctx: DirectiveContext) -> None:
        """
        Extend a configuration with another configuration asynchronously.
        For more information see `extend`.
        """
        await self._substitute_async(ctx, preprocess=True, preserve=True)

    @directive(Directives.INCLUDE)
    async def include_async(self, ctx: DirectiveContext) -> None:
        """
        Include a configuration in another configuration asynchronously.
        For more information see `include`.
        """
        await self._substitute_async(ctx, preprocess=True, preserve=False)

    @directive(Directives.COPY)
    async def copy_async(self, ctx: DirectiveContext) -> None:
        """
        Copy a configuration and paste into another configuration asynchronously.
        For more information see `copy`.
        """
        await self._substitute_async(ctx, preprocess=False, preserve=False)

    def _get_substitution_means(
        self,
        ctx: DirectiveContext,  # , *, preserve: bool
    ) -> tuple[
        ConfigAgent[ConfigModelT],
        ConfigAgent[ConfigModelT],
        ConfigRouteLike | None,
    ]:
        agent_class = type(self.agent)

        # TODO(bswck): raise on include and extend combined  # noqa: TD003
        # if preserve and ???:
        #         "Using more than one ??? directive "
        #         "in the same scope is not allowed"

        agent, route = agent_class.from_directive_context(
            ctx,
            route_separator=self.route_separator,
        )

        if agent.resource == self.agent.resource:
            msg = f"{agent.resource} tried to {ctx.directive!r} on itself"
            raise ConfigPreprocessingError(msg)

        actual_agent = agent
        if agent.is_relative:
            parent = cast(pathlib.Path, self.agent.resource).parent
            child = cast(pathlib.Path, agent.resource)

            actual_agent = copy.copy(agent)
            actual_agent.resource = parent / child

        return actual_agent, agent, route

    def _substitute(
        self,
        ctx: DirectiveContext,
        *,
        preprocess: bool,
        preserve: bool,
    ) -> None:
        agent, orig_agent, route = self._get_substitution_means(ctx)

        with agent.processor_open_resource() as reader:
            source = orig_agent.load_dict(reader.read(), preprocess=preprocess)

        self._substitute_impl(
            ctx,
            route,
            source=source,
            agent=orig_agent,
            preprocess=preprocess,
            preserve=preserve,
        )

    async def _substitute_async(
        self,
        ctx: DirectiveContext,
        *,
        preprocess: bool,
        preserve: bool,
    ) -> None:
        agent, orig_agent, route = self._get_substitution_means(ctx)

        async with agent.processor_open_resource_async() as reader:
            source = orig_agent.load_dict(await reader.read(), preprocess=preprocess)

        self._substitute_impl(
            ctx,
            route,
            source=source,
            agent=orig_agent,
            preprocess=preprocess,
            preserve=preserve,
        )

    @staticmethod
    def _substitute_impl(
        ctx: DirectiveContext,
        route: ConfigRouteLike | None,
        *,
        source: dict[str, Any],
        agent: ConfigAgent[ConfigModelT],
        preprocess: bool,
        preserve: bool,
    ) -> None:
        from configzen.model import CONTEXT, Context, at

        if route:
            source = at(source, route, agent=agent)
            if not is_dict_like(source):
                msg = (
                    f"imported item {route!r} from {agent.resource} is not a dictionary"
                )
                raise ConfigPreprocessingError(msg)

        context: Context[ConfigModelT] = Context(agent)
        ctx.container = {**source, **ctx.container}

        if preserve:
            ctx.container.update(
                {
                    CONTEXT: context,
                    EXPORT: ExportMetadata(
                        route=str(route),
                        context=context,
                        preprocess=preprocess,
                        key_order=list(ctx.container),
                    ),
                },
            )

    @classmethod
    def _export(  # noqa: D417
        cls,
        state: dict[str, Any],
        metadata: ExportMetadata[ConfigModelT],
    ) -> None:
        """
        Export model state preserving substition directive calls in the model state.

        Parameters
        ----------
        metadata
        state
        """
        from configzen.model import CONTEXT, at, export_hook

        overrides = {}

        route = metadata["route"]
        context = metadata["context"]
        key_order = metadata["key_order"]
        agent = context.agent

        with agent.processor_open_resource() as reader:
            # Here we intentionally always preprocess the loaded configuration.
            loaded = agent.load_dict(reader.read())

            if route:
                loaded = at(loaded, route, agent=agent)

        substituted_values = loaded.copy()

        for key, value in loaded.items():
            counterpart_value = state.pop(key, Undefined)
            if counterpart_value is Undefined:
                continue
            counterpart_value = export_hook(counterpart_value)

            if is_dict_like(value):
                if EXPORT in value:
                    value.pop(CONTEXT, None)
                    cls.export(value, metadata=value.pop(EXPORT))
                overrides_for_key = {
                    sub_key: comp
                    for sub_key, comp in counterpart_value.items()
                    if (
                        (orig := value.get(sub_key, Undefined)) is Undefined
                        or orig != comp
                    )
                }
                if overrides_for_key:
                    export_key = agent.processor_class.extension_prefix + key
                    overrides[export_key] = overrides_for_key

            elif is_list_like(value):
                cls.export(value)
                if value != counterpart_value:
                    overrides[key] = counterpart_value

            elif value != counterpart_value:
                overrides[key] = counterpart_value
                del substituted_values[key]

        for value in state.values():
            cls.export(value)

        cls._export_finalize(
            context=context,
            state=state,
            overrides=overrides,
            values=substituted_values,
            route=route,
            key_order=key_order,
        )

    @classmethod
    async def _export_async(
        cls,
        state: dict[str, Any],
        metadata: ExportMetadata[ConfigModelT],
    ) -> None:
        """
        Export model state preserving substition directive calls in the model state.

        Parameters
        ----------
        metadata
            Metadata for exporting that contains initialloading data,
            such as the initial key order, agent used, context, route etc.
        state
            The state to export.
        """
        from configzen.model import CONTEXT, at, export_hook

        overrides = {}

        route = metadata["route"]
        context = metadata["context"]
        key_order = metadata["key_order"]
        agent = context.agent

        async with agent.processor_open_resource_async() as reader:
            # Here we intentionally always preprocess the loaded configuration.
            loaded = await agent.load_dict_async(await reader.read())

            if route:
                loaded = at(loaded, route, agent=agent)

        substituted_values = loaded.copy()

        for key, value in loaded.items():
            counterpart_value = state.pop(key, Undefined)
            if counterpart_value is Undefined:
                continue
            counterpart_value = export_hook(counterpart_value)

            if is_dict_like(value):
                if EXPORT in value:
                    value.pop(CONTEXT, None)
                    await cls.export_async(value, metadata=value.pop(EXPORT))
                overrides_for_key = {
                    sub_key: comp
                    for sub_key, comp in counterpart_value.items()
                    if (
                        (orig := value.get(sub_key, Undefined)) is Undefined
                        or orig != comp
                    )
                }
                if overrides_for_key:
                    export_key = agent.processor_class.extension_prefix + key
                    overrides[export_key] = overrides_for_key

            elif is_list_like(value):
                await cls.export_async(value)
                if value != counterpart_value:
                    overrides[key] = counterpart_value

            elif counterpart_value != value:
                overrides[key] = counterpart_value
                del substituted_values[key]

        for value in state.values():
            await cls.export_async(value)

        cls._export_finalize(
            context=context,
            state=state,
            overrides=overrides,
            values=substituted_values,
            route=route,
            key_order=key_order,
        )

    @classmethod
    def _export_finalize(
        cls,
        context: BaseContext[ConfigModelT],
        *,
        state: dict[str, Any],
        overrides: dict[str, Any],
        values: dict[str, Any],
        route: str | None,
        key_order: list[str],
    ) -> None:
        from configzen.model import export_hook

        state.update(overrides)
        extras: dict[str, Any] = {
            key: state.pop(key) for key in set(state) if key not in key_order
        }

        if values:
            substitution_directive = cls.directive(Directives.EXTEND)
            resource = str(export_hook(context.agent.resource))
            if route:
                resource = cls.route_separator.join((resource, route))
            # Put the substitution directive at the beginning of the state in-place.
            state.update(
                {
                    substitution_directive: resource,
                    **{key: state.pop(key) for key in set(state)},
                },
            )

        # Preserve the order of keys in the original configuration.
        for key in filter(state.__contains__, key_order):
            state[key] = state.pop(key)

        state.update(extras)
