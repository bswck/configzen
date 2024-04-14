"""`configzen.configuration`: the base configuration model, `BaseConfiguration`."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any, ClassVar, cast

from anyio.to_thread import run_sync
from pydantic import BaseModel, PrivateAttr
from pydantic._internal._config import config_keys as pydantic_config_keys
from pydantic._internal._model_construction import ModelMetaclass
from pydantic_settings import BaseSettings
from pydantic_settings.main import SettingsConfigDict

from configzen.context import copy_context_on_await, copy_context_on_call
from configzen.data import roundtrip_update_mapping
from configzen.replacements import ReplacementParser
from configzen.routes import (
    EMPTY_ROUTE,
    GetAttr,
    GetItem,
    LinkedRoute,
    Route,
    RouteLike,
    advance_linked_route,
)
from configzen.sources import ConfigurationSource, get_configuration_source

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pydantic import ConfigDict as BaseConfigDict
    from typing_extensions import Self, Unpack

    from configzen.data import Data
    from configzen.routes import Step


__all__ = ("BaseConfiguration", "ModelConfig")


class ModelConfig(SettingsConfigDict, total=False):
    """Meta-configuration for configzen models."""

    configuration_source: ConfigurationSource[Any, Any]
    rebuild_on_load: bool
    parser_factory: Callable[..., ReplacementParser]


pydantic_config_keys |= set(ModelConfig.__annotations__)
loading: ContextVar[bool] = ContextVar("loading", default=False)
owner_lookup: ContextVar[BaseConfiguration] = ContextVar("owner")


def _locate(
    owner: object,
    value: object,
    route: Route,
    subconfiguration: BaseConfiguration,
) -> Iterator[Route]:
    # Complex case: a subconfiguration in a subkey.
    if value is owner:
        return
    attribute_access = False
    if isinstance(value, BaseModel):
        value = value.model_dump()
        attribute_access = True
    if isinstance(value, Mapping):
        yield from _locate_in_mapping(
            value,
            subconfiguration,
            route,
            attribute_access=attribute_access,
        )  # Complex case: a subconfiguration in an iterable.
    elif isinstance(value, Iterable):
        yield from _locate_in_iterable(value, subconfiguration, route)


def _locate_in_mapping(
    data: Mapping[Any, Any],
    subconfiguration: BaseConfiguration,
    base_route: Route = EMPTY_ROUTE,
    *,
    attribute_access: bool = False,
) -> Iterator[Route]:
    for key, value in data.items():
        # Simple case: a subconfiguration at the current key.
        route = base_route.enter(GetAttr(key) if attribute_access else GetItem(key))
        if value is subconfiguration:
            yield route
            continue
        # Complex case.
        yield from _locate(
            owner=data,
            value=value,
            route=route,
            subconfiguration=subconfiguration,
        )


def _locate_in_iterable(
    data: Iterable[object],
    subconfiguration: BaseConfiguration,
    base_route: Route = EMPTY_ROUTE,
) -> Iterator[Route]:
    for idx, value in enumerate(data):
        # Simple case: a subconfiguration at the current index.
        route = base_route.enter(GetItem(idx))
        if value is subconfiguration:
            yield route
            continue
        # Complex case.
        yield from _locate(
            owner=data,
            value=value,
            route=route,
            subconfiguration=subconfiguration,
        )


class BaseConfigurationMetaclass(ModelMetaclass):
    model_config: ModelConfig

    if not TYPE_CHECKING:
        # Allow type-safe route declaration instead of using strings.
        # Instead of writing conf.at("foo"), we can write conf.at(Conf.foo)
        # to ensure full type safety backed by a membership check at runtime.
        #
        # Shoutout to Micael Jarniac for the suggestion.

        def __getattr__(self, name: str) -> Any:
            if name in self.model_fields:
                return LinkedRoute(self, GetAttr(name))
            raise AttributeError(name)


class BaseConfiguration(BaseSettings, metaclass=BaseConfigurationMetaclass):
    """Base class for all configuration models."""

    _configuration_source: ConfigurationSource[Any, Any] = PrivateAttr()
    _configuration_data: Data = PrivateAttr(default_factory=dict)
    _configuration_parser: ReplacementParser = PrivateAttr()
    _configuration_root: BaseConfiguration | None = PrivateAttr(default=None)

    def __init__(self, **data: Any) -> None:
        try:
            owner = owner_lookup.get()
        except LookupError:
            owner = None
            if loading.get():
                owner_lookup.set(self)
        super().__init__(**data)
        self._configuration_root = owner

    # Mark the configzen's constructor as a non-custom constructor.
    __init__.__pydantic_base_init__ = True  # type: ignore[attr-defined]

    @property
    def configuration_root(self) -> BaseConfiguration:
        """Return the root configuration that was used to load the entire data."""
        return self._configuration_root or self

    @property
    def configuration_source(self) -> ConfigurationSource[Any, Any] | None:
        """Return the configuration source that was used to load the configuration."""
        if self._configuration_root is None:
            # Since _configuration_source is a private attribute
            # without a default value, we need to use getattr
            # to avoid an AttributeError in case this attribute
            # was not set (which may happen when the configuration
            # is instantiated manually).
            return getattr(self, "_configuration_source", None)
        return self._configuration_root.configuration_source

    @property
    def configuration_data(self) -> Data:
        """Return the configuration that was loaded from the configuration source."""
        if self._configuration_root is None:
            return self._configuration_data
        return self._configuration_root.configuration_data

    @property
    def configuration_parser(self) -> ReplacementParser:
        """
        Current replacement parser.

        Parser stores the initial data used when loading the configuration,
        resolves macros etc.
        """
        if self._configuration_root is None:
            if not hasattr(self, "_configuration_parser"):
                return ReplacementParser(self.configuration_dump())
            return self._configuration_parser
        return self._configuration_root.configuration_parser

    def configuration_find_routes(
        self,
        subconfiguration: BaseConfiguration,
    ) -> set[Route]:
        """
        Locate all occurrences of a subconfiguration in the current configuration.

        Return a set of routes to the located subconfiguration.
        """
        if not isinstance(subconfiguration, BaseConfiguration):
            msg = (
                f"Expected a BaseConfiguration subclass, got {type(subconfiguration)!r}"
            )
            raise TypeError(msg)
        return set(
            _locate_in_mapping(
                self.__dict__,
                subconfiguration,
                attribute_access=True,
            ),
        )

    find_routes = configuration_find_routes

    def configuration_find_route(self, subconfiguration: BaseConfiguration) -> Route:
        """Locate exactly one (closest) route to the given subconfiguration."""
        all_routes = self.configuration_find_routes(subconfiguration)
        if not all_routes:
            msg = f"Unable to locate subconfiguration {subconfiguration}"
            raise LookupError(msg)
        return next(iter(all_routes))

    find_route = configuration_find_route

    @classmethod
    def _validate_configuration_source(
        cls,
        source: object | None = None,
    ) -> ConfigurationSource[Any, Any]:
        if source is None:
            source = cls.model_config.get("configuration_source")
        if source is None:
            msg = f"No config source provided when loading {cls.__name__}"
            raise ValueError(msg)
        if not isinstance(source, ConfigurationSource):
            source = get_configuration_source(source)
            if source is None:
                msg = (
                    f"Could not create a config source from {source!r} "
                    f"of type {type(source)!r}"
                )
                raise ValueError(msg)
        return source

    @classmethod
    def _validate_parser_factory(
        cls,
        parser_factory: Callable[..., ReplacementParser] | None = None,
    ) -> Callable[..., ReplacementParser]:
        return (
            parser_factory
            or cast(
                "Callable[..., ReplacementParser] | None",
                cls.model_config.get("configuration_parser_factory"),
            )
            or ReplacementParser
        )

    @classmethod
    @copy_context_on_call
    def configuration_load(
        cls,
        source: object | None = None,
        *,
        parser_factory: Callable[..., ReplacementParser] | None = None,
    ) -> Self:
        """
        Load this configuration from a given source.

        Parameters
        ----------
        source
            Where to load the configuration from. The argument passed is forwarded
            to `confizen.sources.get_configuration_source()` which will resolve
            the intended configuration source: for example, "abc.ini" will be resolved
            to a TOML text file source. Keep in mind, however, that for binary formats
            such as non-XML Plist you must specify its format type to binary, so in
            that case just create `BinaryFileConfigurationSource("plist_file.plist")`.
        context
            The context to use during model validation.
            See also https://docs.pydantic.dev/latest/api/base_model @ `model_validate`.
        parser_factory
            The state factory to use to parse the newly loaded configuration data.

        Returns
        -------
        self

        """
        if cls.model_config["rebuild_on_load"]:
            # Frame 1: copy_context_and_call.<locals>.copy()
            # Frame 2: copy_and_run()
            # Frame 3: <class>.configuration_load()
            # Frame 4: <class>.model_rebuild()
            cls.model_rebuild(_parent_namespace_depth=4)

        # Validate the source we load our configuration from.
        configuration_source = cls._validate_configuration_source(source)

        # Validate the parser we use to parse the loaded configuration data.
        make_parser = cls._validate_parser_factory(parser_factory)

        # Load the configuration data from the sanitized source.
        # Keep in mind the loaded data object keeps all the additional
        # metadata that we want to keep.
        # Then we pass it to the parser factory to process the configuration data
        # into a bare dictionary that does not hold anything else
        # than the configuration data, by using `parser.get_processed_data()`.
        parser = make_parser(configuration_source.load())

        # ruff: noqa: FBT003
        try:
            loading.set(True)

            # Processing will execute any commands that are present
            # in the configuration data and return the final configuration
            # data that we will use to construct an instance of the configuration model.
            # During this process, we lose all the additional metadata that we
            # want to keep in the configuration data.
            # They will be added back to the exported data when the configuration
            # is saved (`parser.revert_parser_changes()`).
            self = cls(**parser.get_data_with_replacements())
        finally:
            loading.set(False)

        # Quick setup and we're done.
        self._configuration_source = configuration_source
        self._configuration_parser = parser
        return self

    @classmethod
    def load(cls, source: object | None = None, **kwargs: Any) -> Self:
        """Do the same as `configuration_load`."""
        return cls.configuration_load(source, **kwargs)

    @classmethod
    @copy_context_on_await
    @wraps(configuration_load)
    async def configuration_load_async(
        cls,
        source: object | None = None,
        *,
        parser_factory: Callable[..., ReplacementParser] | None = None,
    ) -> Self:
        """
        Do the same as `configuration_load`, but asynchronously (no I/O blocking).

        Parameters
        ----------
        source
            Where to load the configuration from. The argument passed is forwarded
            to `confizen.sources.get_configuration_source()` which will resolve
            the intended configuration source: for example, "abc.ini" will be resolved
            to a TOML text file source. Keep in mind, however, that for binary formats
            such as non-XML Plist you must specify its format type to binary, so in
            that case just create `BinaryFileConfigurationSource("plist_file.plist")`.
        parser_factory
            The state factory to use to parse the newly loaded configuration data.

        Returns
        -------
        self

        """
        # Intentionally not using `run_sync(configuration_load)` here.
        # We want to keep every user-end object handled by the same thread.

        if cls.model_config["rebuild_on_load"]:
            # Frame 1: copy_context_on_await.<locals>.copy_async()
            # Frame 2: copy_and_await()
            # Frame 3: <class>.configuration_load_async()
            # Frame 4: <class>.model_rebuild()
            cls.model_rebuild(_parent_namespace_depth=4)

        configuration_source = cls._validate_configuration_source(source)
        make_parser = cls._validate_parser_factory(parser_factory)
        parser = make_parser(await configuration_source.load_async())

        try:
            loading.set(True)

            # Since `parser.get_processed_data()` operates on primitive data types,
            # we can safely use run_sync here to run in a worker thread.
            self = cls(**await run_sync(parser.get_data_with_replacements))
        finally:
            loading.set(False)

        self._configuration_parser = parser
        self._configuration_source = configuration_source
        return self

    @classmethod
    @wraps(configuration_load_async)
    async def load_async(cls, source: object | None = None, **kwargs: Any) -> Self:
        """Do the same as `configuration_load_async`."""
        return await cls.configuration_load_async(source, **kwargs)

    def configuration_reload(self) -> Self:
        """Reload the configuration from the same source."""
        source = self.configuration_source

        if source is None:
            msg = "Cannot reload a manually instantiated configuration"
            raise RuntimeError(msg)

        root = self.configuration_root

        # Create a new parser with the same options as the current one.
        parser = root.configuration_parser.create_parser(source.load())

        # Construct a new configuration instance.
        # Respect __class__ attribute in case root might be a proxy (from proxyvars).
        new_root = root.__class__(**parser.get_data_with_replacements())

        # Copy values from the freshly loaded configuration into our instance.
        if root is self:
            new_data = new_root.configuration_dump()
        else:
            route_to_self = root.configuration_find_route(self)
            new_data = cast("Self", route_to_self.get(new_root)).configuration_dump()

        for key, value in new_data.items():
            setattr(self, key, value)

        return self

    @wraps(configuration_reload)
    def reload(self) -> Self:
        """Do the same as `configuration_reload`."""
        return self.configuration_reload()

    async def configuration_reload_async(self) -> Self:
        """Do the same as `configuration_reload` asynchronously (no I/O blocking)."""
        source = self.configuration_source

        if source is None:
            msg = "Cannot reload a manually instantiated configuration"
            raise RuntimeError(msg)

        root = self.configuration_root

        # Create a new state parser the same options as the current one.
        parser = root.configuration_parser.create_parser(source.load())

        # Construct a new configuration instance.
        new_root = root.__class__(**await run_sync(parser.get_data_with_replacements))

        # Copy values from the freshly loaded configuration into our instance.
        if root is self:
            new_data = new_root.configuration_dump()
        else:
            route_to_self = root.configuration_find_route(self)
            new_data = cast("Self", route_to_self.get(new_root)).configuration_dump()

        for key, value in new_data.items():
            setattr(self, key, value)

        return self

    @wraps(configuration_reload_async)
    async def reload_async(self) -> Self:
        """Do the same as `configuration_reload_async`."""
        return await self.configuration_reload_async()

    def _configuration_data_save(
        self,
        destination: object | None = None,
    ) -> tuple[ConfigurationSource[Any, Any], Data]:
        if destination is None:
            configuration_destination = self.configuration_source
        else:
            configuration_destination = self._validate_configuration_source(destination)

        if configuration_destination is None:
            msg = "Cannot save configuration (source/destination unknown)"
            raise RuntimeError(msg)

        root = self.configuration_root
        parser = self.configuration_parser

        if root is self:
            new_data = self.configuration_dump()
        else:
            # Construct a new configuration instance.
            # Respect __class__ attribute since root might be a proxy (from proxyvars).
            new_root = root.__class__(**parser.get_data_with_replacements())
            routes = root.configuration_find_routes(self)

            for route in routes:
                route.set(new_root, self)

            new_data = new_root.configuration_dump()

        parsed_data = parser.get_data_with_replacements()
        roundtrip_update_mapping(roundtrip_data=parsed_data, mergeable_data=new_data)
        flat_new_data = parsed_data.revert_replacements()

        data = parser.roundtrip_initial
        configuration_destination.data_format.roundtrip_update_mapping(
            roundtrip_data=data,
            mergeable_data=flat_new_data,
        )
        return configuration_destination, data

    def configuration_save(self, destination: object | None = None) -> Self:
        """
        Save the configuration to a given destination.

        Parameters
        ----------
        destination
            Where to save the configuration to. The argument passed is forwarded
            to `confizen.sources.get_configuration_source()` which will resolve
            the intended configuration source: for example, "abc.ini" will be resolved
            to a TOML text file source. Keep in mind, however, that for binary formats
            such as non-XML Plist you must specify its format type to binary, so in
            that case just create `BinaryFileConfigurationSource("plist_file.plist")`.

        """
        configuration_source, data = self._configuration_data_save(destination)
        configuration_source.dump(data)
        return self

    @wraps(configuration_save)
    def save(self, destination: object | None = None) -> Self:
        """Do the same as `configuration_save`."""
        return self.configuration_save(destination)

    async def configuration_save_async(self, destination: object | None = None) -> Self:
        """
        Do the same as `configuration_save`, but asynchronously (no I/O blocking).

        Parameters
        ----------
        destination
            Where to save the configuration to. The argument passed is forwarded
            to `confizen.sources.get_configuration_source()` which will resolve
            the intended configuration source: for example, "abc.ini" will be resolved
            to a TOML text file source. Keep in mind, however, that for binary formats
            such as non-XML Plist you must specify its format type to binary, so in
            that case just create `BinaryFileConfigurationSource("plist_file.plist")`.

        """
        configuration_source, data = self._configuration_data_save(destination)
        await configuration_source.dump_async(data)
        return self

    @wraps(configuration_save_async)
    async def save_async(self, destination: object | None = None) -> Self:
        """Do the same as `configuration_save_async`."""
        return await self.configuration_save_async(destination)

    def configuration_at(self, *routes: RouteLike) -> Item:
        """Return a configuration item at the given set of routes."""
        return Item(routes=set(map(Route, routes)), configuration=self)

    @wraps(configuration_at)
    def at(self, *routes: RouteLike) -> Item:
        """Do the same as `configuration_at`."""
        return self.configuration_at(*routes)

    def configuration_dump(self) -> dict[str, object]:
        """Return a dictionary representation of the configuration."""
        return super().model_dump()

    @wraps(configuration_dump)
    def dump(self) -> dict[str, object]:
        """Do the same as `configuration_dump`."""
        return self.configuration_dump()

    def __getitem__(self, routes: RouteLike | tuple[RouteLike, ...]) -> Item:
        """Return a configuration item at the given set of routes."""
        if isinstance(routes, tuple):
            return self.configuration_at(*routes)
        return self.configuration_at(routes)

    def __setitem__(self, item: RouteLike, value: Any) -> None:
        """Set a configuration item at the given set of routes."""
        self.configuration_at(item).configuration = value

    def __init_subclass__(cls, **kwargs: Unpack[ModelConfig]) -> None:
        """Initialize the configuration subclass."""
        super().__init_subclass__(**cast("BaseConfigDict", kwargs))

    model_config: ClassVar[ModelConfig] = ModelConfig(
        # Be lenient about forward references.
        rebuild_on_load=True,
        # Keep the configuration valid & fail-proof for the whole time.
        validate_assignment=True,
        # Make it easier to spot typos.
        extra="forbid",
    )


@advance_linked_route.register(BaseConfiguration)
def configuration_step(
    owner: type[BaseConfiguration],
    __annotation: Any,
    step: Step[Any],
) -> Any:
    """Return the value of a configuration attribute."""
    return owner.model_fields[step.key].annotation


@dataclass
class Item:
    routes: set[Route]
    configuration: BaseConfiguration

    def __getitem__(self, item: RouteLike) -> Item:
        return self.configuration.configuration_at(
            *(route.enter(item) for route in self.routes),
        )

    def __setitem__(self, item: RouteLike, value: Any) -> None:
        for route in self.routes:
            route.enter(item).set(self.configuration, value)
