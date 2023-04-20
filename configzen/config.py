import collections
import inspect
from io import StringIO
from typing import Any, Protocol, TypeVar, runtime_checkable, Awaitable
from urllib.parse import uses_relative, uses_netloc, uses_params, urlparse
from urllib.request import urlopen

from configzen.engine import get_engine_class, Engine, to_dict


T = TypeVar('T')

_URL_SCHEMES = set(uses_relative + uses_netloc + uses_params) - {''}


@runtime_checkable
class Readable(Protocol[T]):
    def read(self) -> T:
        ...


class ConfigSpec:
    def __init__(
        self,
        filepath_or_buffer: Readable | str = None,
        engine_name: str = 'yaml',
        cache_engine: bool = True,
        defaults: dict[str, Any] | None = None,
        **engine_options: Any,
    ):
        self.filepath_or_buffer = filepath_or_buffer
        self.defaults = defaults
        self.engine_name = engine_name
        self._engine = None
        self._engine_options = engine_options
        if cache_engine:
            self._engine = get_engine_class(self.engine_name)(**engine_options)
        self.cache_engine = cache_engine

    def _get_engine(self) -> Engine:
        engine = self._engine
        if engine is None:
            engine_class = get_engine_class(self.engine_name)
            engine = engine_class(**self._engine_options)
        if self.cache_engine:
            self._engine = engine
        return engine

    @property
    def engine(self) -> Engine:
        return self._get_engine()

    @property
    def is_url(self) -> bool:
        return (
            isinstance(self.filepath_or_buffer, str)
            and urlparse(self.filepath_or_buffer).scheme in _URL_SCHEMES
        )

    @classmethod
    def from_str(cls, spec: str) -> 'ConfigSpec':
        return cls(spec)

    def open(self, **kwds) -> Readable[bytes | str]:
        if self.filepath_or_buffer is None:
            return StringIO()
        if self.is_url:
            return urlopen(self.filepath_or_buffer, **kwds)
        return open(self.filepath_or_buffer, **kwds)

    def read(self, **kwds) -> dict[str, Any] | Awaitable[dict[str, Any]]:
        with self.open(**kwds) as fp:
            serialized_data = fp.read()
        return self.engine.load(serialized_data, defaults=self.defaults)


class DispatchStrategy:
    def __init__(
        self,
        schema: dict[str, Any] | None = None,
        /, **schema_kwds: Any
    ):
        if schema and schema_kwds:
            raise ValueError('Must provide either schema or schema_kwds')
        self.schema = schema or schema_kwds
        self.asynchronous = False

    async def _async_dispatch(self, data: dict[str, Any], parent: 'Config | None' = None):
        raise NotImplementedError

    def _dispatch(self, data: dict[str, Any], parent: 'Config | None' = None):
        raise NotImplementedError

    def dispatch(self, data: dict[str, Any], parent: 'Config | None' = None):
        if self.asynchronous:
            return self._async_dispatch(data, parent)
        return self._dispatch(data, parent)


class SimpleDispatcher(DispatchStrategy):
    def _create_item(self, key, value, parent=None):
        item = self.schema[key]
        if hasattr(item, '__configzen_create__'):
            return item.__configzen_create__(key, value, parent)
        return item(value)

    async def _async_create_item(self, key, value, parent=None):
        data = self._create_item(key, value, parent)
        if inspect.isawaitable(data):
            data = await data
        return data

    def _dispatch(self, data, parent=None):
        return {
            key: self._create_item(key, value, parent)
            for key, value in data.items()
        }

    async def _async_dispatch(self, data, parent=None):
        return {
            key: await self._async_create_item(self.schema[key], value, parent)
            for key, value in data.items()
        }


class Config(collections.UserDict):
    def __init__(
        self,
        spec: ConfigSpec | str,
        dispatcher: DispatchStrategy | None = None,
        lazy: bool = False,
        asynchronous: bool | None = None,
        **schema: Any
    ):
        if isinstance(spec, str):
            spec = ConfigSpec.from_str(spec)
        elif isinstance(spec, Readable):
            spec = ConfigSpec(spec)
        self.spec = spec
        self.dispatcher = dispatcher
        self.schema = schema
        if schema and dispatcher:
            raise ValueError('Must provide either dispatcher or **schema')

        if dispatcher:
            self.dispatcher = dispatcher
            self.schema = dispatcher.schema
        else:
            self.dispatcher = SimpleDispatcher(schema)
        if asynchronous is not None:
            self.dispatcher.asynchronous = asynchronous

        super().__init__()

        if not asynchronous and not lazy:
            self.load()

    def __await__(self):
        return self.load()

    def __call__(self, **config):
        objects = self.dispatcher.dispatch(config)
        if self.asynchronous:
            async def coro():
                nonlocal objects
                if inspect.isawaitable(objects):
                    objects = await objects
                self.data.update(objects)
                return self
            return coro()
        self.data.update(objects)
        return self

    @property
    def asynchronous(self) -> bool:
        return self.dispatcher.asynchronous

    def load(self, **kwargs):
        data = self.spec.read(**kwargs)

        if self.asynchronous:
            async def async_read():
                nonlocal data
                if inspect.isawaitable(data):
                    data = await data
                return await self(**data)
            return async_read()

        return self(**data)

    reload = load

    def __configzen_to_dict__(self):
        return self.data

    def save(self, **kwargs):
        serialized_data = self.spec.engine.dump(to_dict(self.data))
        if self.spec.is_url:
            # imagine that!
            # todo(bswck)
            raise NotImplementedError('Saving to URLs is not yet supported')
        with self.spec.open(mode='w', **kwargs) as fp:
            fp.write(serialized_data)

    def __getattr__(self, item):
        try:
            return self.data[item]
        except KeyError:
            raise AttributeError(
                f'{type(self).__name__!r} object has no attribute {item}'
            ) from None


class Subconfig:
    def __init__(self, parent: Config, key: str):
        self.parent = parent
        self.key = key

    # def set(self, value):
    #     return self.parent.set(self.key, value)
    #
    # def save(self, **kwargs):
    #     serialized_data = self.spec.engine.dump(self.to_dict())
    #     if self.spec.is_url:
    #         # imagine that!
    #         # todo(bswck)
    #         raise NotImplementedError('Saving to URLs is not yet supported')
    #     with self.spec.open(mode='w', **kwargs) as fp:
    #         fp.write(serialized_data)
