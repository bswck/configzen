"""The base configuration model class [`BaseConfig`][configzen.config.BaseConfig]."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, cast

from anyio.to_thread import run_sync
from pydantic import BaseModel, PrivateAttr
from pydantic._internal._config import config_keys as pydantic_config_keys
from pydantic._internal._model_construction import ModelMetaclass
from pydantic_settings import BaseSettings
from pydantic_settings.main import SettingsConfigDict

from configzen.context import isolated_context_coroutine, isolated_context_function
from configzen.data import roundtrip_update_mapping
from configzen.processor import ConfigProcessor, FileSystemAwareConfigProcessor
from configzen.routes import (
    EMPTY_ROUTE,
    GetAttr,
    GetItem,
    LinkedRoute,
    Route,
    RouteLike,
    advance_linked_route,
)
from configzen.sources import ConfigSource, get_config_source

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pydantic import ConfigDict as BaseConfigDict
    from typing_extensions import Self, Unpack

    from configzen.data import Data
    from configzen.routes import Step


__all__ = ("BaseConfig", "ModelConfig")


class ModelConfig(SettingsConfigDict, total=False):
    """Meta-configuration for configzen models."""

    config_source: str | ConfigSource[Any, Any]
    rebuild_on_load: bool
    processor_factory: Callable[..., ConfigProcessor]


pydantic_config_keys |= set(ModelConfig.__annotations__)
processing: ContextVar[ProcessingContext | None] = ContextVar(
    "processing",
    default=None,
)
owner_lookup: ContextVar[BaseConfig] = ContextVar("owner")


class ProcessingContext(NamedTuple):
    model_class: type[BaseConfig]
    processor: ConfigProcessor
    # We keep it mainly to make path resolution smarter.
    trace: list[ConfigSource[Any, Any]]


def _locate(
    owner: object,
    value: object,
    route: Route,
    subconfig: BaseConfig,
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
            subconfig,
            route,
            attribute_access=attribute_access,
        )  # Complex case: a subconfiguration in an iterable.
    elif isinstance(value, Iterable):
        yield from _locate_in_iterable(value, subconfig, route)


def _locate_in_mapping(
    data: Mapping[Any, Any],
    subconfig: BaseConfig,
    base_route: Route = EMPTY_ROUTE,
    *,
    attribute_access: bool = False,
) -> Iterator[Route]:
    for key, value in data.items():
        # Simple case: a subconfiguration at the current key.
        route = base_route.enter(GetAttr(key) if attribute_access else GetItem(key))
        if value is subconfig:
            yield route
            continue
        # Complex case.
        yield from _locate(
            owner=data,
            value=value,
            route=route,
            subconfig=subconfig,
        )


def _locate_in_iterable(
    data: Iterable[object],
    subconfig: BaseConfig,
    base_route: Route = EMPTY_ROUTE,
) -> Iterator[Route]:
    for idx, value in enumerate(data):
        # Simple case: a subconfiguration at the current index.
        route = base_route.enter(GetItem(idx))
        if value is subconfig:
            yield route
            continue
        # Complex case.
        yield from _locate(
            owner=data,
            value=value,
            route=route,
            subconfig=subconfig,
        )


class BaseConfigMetaclass(ModelMetaclass):
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


class BaseConfig(BaseSettings, metaclass=BaseConfigMetaclass):
    """Base class for all configuration models."""

    _config_source: ConfigSource[Any, Any] = PrivateAttr()
    _config_data: Data = PrivateAttr(default_factory=dict)
    _config_processor: ConfigProcessor = PrivateAttr()
    _config_root: BaseConfig | None = PrivateAttr(default=None)

    def __init__(self, **data: Any) -> None:
        try:
            owner = owner_lookup.get()
        except LookupError:
            owner = None
            if processing.get():
                owner_lookup.set(self)
        super().__init__(**data)
        self._config_root = owner

    # Mark the configzen's constructor as a non-custom constructor.
    __init__.__pydantic_base_init__ = True  # type: ignore[attr-defined]

    @property
    def config_root(self) -> BaseConfig:
        """Return the root configuration that was used to load the entire data."""
        return self._config_root or self

    @property
    def config_source(self) -> ConfigSource[Any, Any] | None:
        """Return the configuration source that was used to load the configuration."""
        if self._config_root is None:
            # Since _config_source is a private attribute
            # without a default value, we need to use getattr
            # to avoid an AttributeError in case this attribute
            # was not set (which may happen when the configuration
            # is instantiated manually).
            return getattr(self, "_config_source", None)
        return self._config_root.config_source

    @property
    def config_data(self) -> Data:
        """Return the configuration that was loaded from the configuration source."""
        if self._config_root is None:
            return self._config_data
        return self._config_root.config_data

    @property
    def config_processor(self) -> ConfigProcessor:
        """
        Current configuration processor.

        Processor stores the initial data used when loading the configuration,
        resolves macros etc.
        """
        if self._config_root is None:
            if not hasattr(self, "_config_processor"):
                return FileSystemAwareConfigProcessor(self.config_dump())
            return self._config_processor
        return self._config_root.config_processor

    def config_find_routes(
        self,
        subconfig: BaseConfig,
    ) -> set[Route]:
        """
        Locate all occurrences of a subconfiguration in the current configuration.

        Return a set of routes to the located subconfiguration.
        """
        if not isinstance(subconfig, BaseConfig):
            msg = f"Expected a BaseConfig subclass instance, got {type(subconfig)!r}"
            raise TypeError(msg)
        return set(
            _locate_in_mapping(vars(self), subconfig, attribute_access=True),
        )

    def config_find_route(self, subconfig: BaseConfig) -> Route:
        """Locate exactly one (closest) route to the given subconfiguration."""
        all_routes = self.config_find_routes(subconfig)
        if not all_routes:
            msg = f"Unable to locate subconfiguration {subconfig}"
            raise LookupError(msg)
        return next(iter(all_routes))

    @classmethod
    def _validate_config_source(
        cls,
        source: object | None = None,
    ) -> ConfigSource[Any, Any]:
        if source is None:
            source = cls.model_config.get("config_source")
        if source is None:
            msg = f"No config source provided when loading {cls.__name__}"
            raise ValueError(msg)
        if not isinstance(source, ConfigSource):
            source = get_config_source(source)
            if source is None:
                msg = (
                    f"Could not create a config source from {source!r} "
                    f"of type {type(source)!r}"
                )
                raise ValueError(msg)
        return source

    @classmethod
    def _validate_processor_factory(
        cls,
        processor_factory: Callable[..., ConfigProcessor] | None = None,
    ) -> Callable[..., ConfigProcessor]:
        return (
            processor_factory
            or cast(
                "Callable[..., ConfigProcessor] | None",
                cls.model_config.get("config_processor_factory"),
            )
            or FileSystemAwareConfigProcessor
        )

    @classmethod
    def _try_rebuild_model(cls) -> None:
        # Possible scenarios:
        # (sync) Frame 1: <class>.config_load()
        # (sync) Frame 2: isolated_context_function.<locals>.copy()
        # (sync) Frame 3: run_isolated()
        # (sync) Frame 4: <class>.config_load()
        # (sync) Frame 5: <class>.model_rebuild()
        #
        # (async) Frame 1: <class>.config_load_async()
        # (async) Frame 2: isolated_context_function.<locals>.copy()
        # (async) Frame 3: run_isolated()
        # (async) Frame 4: <class>.config_load()
        # (async) Frame 5: <class>.model_rebuild()
        if cls.model_config["rebuild_on_load"]:
            with suppress(Exception):
                cls.model_rebuild(_parent_namespace_depth=5)

    @classmethod
    @isolated_context_function
    def config_load(
        cls,
        source: object | None = None,
        *,
        processor_factory: Callable[..., ConfigProcessor] | None = None,
    ) -> Self:
        """
        Load this configuration from a given source.

        Parameters
        ----------
        source
            Where to load the configuration from. The argument passed is forwarded
            to `confizen.sources.get_config_source()` which will resolve
            the intended configuration source: for example, "abc.ini" will be resolved
            to a TOML text file source. Keep in mind, however, that for binary formats
            such as non-XML Plist you must specify its format type to binary, so in
            that case just create `BinaryFileConfigSource("plist_file.plist")`.
        processor_factory
            The state factory to use to parse the newly loaded configuration data.

        Returns
        -------
        self

        """
        cls._try_rebuild_model()

        # Validate the source we load our configuration from.
        config_source = cls._validate_config_source(source)

        # Validate the processor we use to parse the loaded configuration data.
        make_processor = cls._validate_processor_factory(processor_factory)

        # Load the configuration data from the sanitized source.
        # Keep in mind the loaded data object keeps all the additional
        # metadata that we want to keep.
        # Then we pass it to the processor factory to process the configuration data
        # into a bare dictionary that does not hold anything else
        # than the configuration data, by using `processor.get_processed_data()`.
        processor = make_processor(config_source.load())

        processing_context = ProcessingContext(cls, processor, trace=[config_source])
        processing.set(processing_context)
        # ruff: noqa: FBT003
        try:
            # Processing will execute any commands that are present
            # in the configuration data and return the final configuration
            # data that we will use to construct an instance of the configuration model.
            # During this process, we lose all the additional metadata that we
            # want to keep in the configuration data.
            # They will be added back to the exported data when the configuration
            # is saved (`processor.revert_processor_changes()`).
            self = cls(**processor.get_processed_data())
        finally:
            processing.set(None)

        # Quick setup and we're done.
        self._config_source = config_source
        self._config_processor = processor
        return self

    @classmethod
    @isolated_context_coroutine
    async def config_load_async(
        cls,
        source: object | None = None,
        *,
        processor_factory: Callable[..., ConfigProcessor] | None = None,
    ) -> Self:
        """
        Do the same as `config_load`, but asynchronously (no I/O blocking).

        Parameters
        ----------
        source
            Where to load the configuration from. The argument passed is forwarded
            to `confizen.sources.get_config_source()` which will resolve
            the intended configuration source: for example, "abc.ini" will be resolved
            to a TOML text file source. Keep in mind, however, that for binary formats
            such as non-XML Plist you must specify its format type to binary, so in
            that case just create `BinaryFileConfig"plist_file.plist")`.
        processor_factory
            The state factory to use to parse the newly loaded configuration data.

        Returns
        -------
        self

        """
        cls._try_rebuild_model()

        # Intentionally not using `run_sync(config_load)` here.
        # We want to keep make the set up instructions blocking to avoid running
        # into mutexes.

        config_source = cls._validate_config_source(source)
        make_processor = cls._validate_processor_factory(processor_factory)
        processor = make_processor(await config_source.load_async())

        processing_context = ProcessingContext(cls, processor, trace=[config_source])
        processing.set(processing_context)
        try:
            self = cls(**await run_sync(processor.get_processed_data))
        finally:
            processing.set(None)

        self._config_processor = processor
        self._config_source = config_source
        return self

    def config_reload(self) -> Self:
        """Reload the configuration from the same source."""
        source = self.config_source

        if source is None:
            msg = "Cannot reload a manually instantiated configuration"
            raise RuntimeError(msg)

        root = self.config_root

        # Create a new processor with the same options as the current one.
        processor = root.config_processor.create_processor(source.load())

        # Construct a new configuration instance.
        # Respect __class__ attribute in case root might be a proxy (from proxyvars).
        new_root = root.__class__(**processor.get_processed_data())

        # Copy values from the freshly loaded configuration into our instance.
        if root is self:
            new_data = new_root.config_dump()
        else:
            route_to_self = root.config_find_route(self)
            new_data = cast("Self", route_to_self.get(new_root)).config_dump()

        for key, value in new_data.items():
            setattr(self, key, value)

        return self

    async def config_reload_async(self) -> Self:
        """Do the same as `config_reload` asynchronously (no I/O blocking)."""
        source = self.config_source

        if source is None:
            msg = "Cannot reload a manually instantiated configuration"
            raise RuntimeError(msg)

        root = self.config_root

        # Create a new state processor the same options as the current one.
        processor = root.config_processor.create_processor(source.load())

        # Construct a new configuration instance.
        new_root = root.__class__(**await run_sync(processor.get_processed_data))

        # Copy values from the freshly loaded configuration into our instance.
        if root is self:
            new_data = new_root.config_dump()
        else:
            route_to_self = root.config_find_route(self)
            new_data = cast("Self", route_to_self.get(new_root)).config_dump()

        for key, value in new_data.items():
            setattr(self, key, value)

        return self

    def _config_data_save(
        self,
        destination: object | None = None,
    ) -> tuple[ConfigSource[Any, Any], Data]:
        if destination is None:
            config_destination = self.config_source
        else:
            config_destination = self._validate_config_source(destination)

        if config_destination is None:
            msg = "Cannot save configuration (source/destination unknown)"
            raise RuntimeError(msg)

        root = self.config_root
        processor = self.config_processor

        if root is self:
            new_data = self.config_dump()
        else:
            # Construct a new configuration instance.
            # Respect `__class__` attribute: root might be a proxy, e.g. from proxyvars.
            new_root = root.__class__(**processor.get_processed_data())
            routes = root.config_find_routes(self)

            for route in routes:
                route.set(new_root, self)

            new_data = new_root.config_dump()

        parsed_data = processor.get_processed_data()
        roundtrip_update_mapping(roundtrip_data=parsed_data, mergeable_data=new_data)
        flat_new_data = parsed_data.revert_replacements()

        data = processor.roundtrip_initial
        config_destination.data_format.roundtrip_update_mapping(
            roundtrip_data=data,
            mergeable_data=flat_new_data,
        )
        return config_destination, data

    def config_save(self, destination: object | None = None) -> Self:
        """
        Save the configuration to a given destination.

        Parameters
        ----------
        destination
            Where to save the configuration to. The argument passed is forwarded
            to `confizen.sources.get_config_source()` which will resolve
            the intended configuration source: for example, "abc.ini" will be resolved
            to a TOML text file source. Keep in mind, however, that for binary formats
            such as non-XML Plist you must specify its format type to binary, so in
            that case just create `BinaryFileConfigSource("plist_file.plist")`.

        """
        config_destination, data = self._config_data_save(destination)
        config_destination.dump(data)
        return self

    async def config_save_async(self, destination: object | None = None) -> Self:
        """
        Do the same as `config_save`, but asynchronously (no I/O blocking).

        Parameters
        ----------
        destination
            Where to save the configuration to. The argument passed is forwarded
            to `confizen.sources.get_config_source()` which will resolve
            the intended configuration source: for example, "abc.ini" will be resolved
            to a TOML text file source. Keep in mind, however, that for binary formats
            such as non-XML Plist you must specify its format type to binary, so in
            that case just create `BinaryFileConfigSource("plist_file.plist")`.

        """
        config_destination, data = self._config_data_save(destination)
        await config_destination.dump_async(data)
        return self

    def config_at(self, *routes: RouteLike) -> Item:
        """Return a configuration item at the given set of routes."""
        return Item(routes=set(map(Route, routes)), config=self)

    def config_dump(self) -> dict[str, object]:
        """Return a dictionary representation of the configuration."""
        return super().model_dump()

    def __getitem__(self, routes: RouteLike | tuple[RouteLike, ...]) -> Item:
        """Return a configuration item at the given set of routes."""
        if isinstance(routes, tuple):
            return self.config_at(*routes)
        return self.config_at(routes)

    def __setitem__(self, item: RouteLike, value: Any) -> None:
        """Set a configuration item at the given set of routes."""
        self.config_at(item).config = value

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


@advance_linked_route.register(BaseConfig)
def config_step(
    owner: type[BaseConfig],
    _annotation: Any,
    step: Step[Any],
) -> Any:
    """Return the value of a configuration attribute."""
    return owner.model_fields[step.key].annotation


@dataclass
class Item:
    routes: set[Route]
    config: BaseConfig

    def __getitem__(self, item: RouteLike) -> Item:
        return self.config.config_at(
            *(route.enter(item) for route in self.routes),
        )

    def __setitem__(self, item: RouteLike, value: Any) -> None:
        for route in self.routes:
            route.enter(item).set(self.config, value)
