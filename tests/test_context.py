from contextvars import ContextVar

import pytest

from configzen.context import (
    async_run_isolated,
    run_isolated,
    isolated_context_coroutine,
    isolated_context_function,
)


def test_copy_context_on_call() -> None:
    cv: ContextVar[int] = ContextVar("cv")
    cv.set(1)

    @isolated_context_function
    def func() -> int:
        cv.set(2)
        return cv.get()

    assert func() == 2
    assert cv.get() == 1

    cv.set(5)

    class Foo:
        @isolated_context_function
        @staticmethod
        def static_method() -> int:
            cv.set(3)
            return cv.get()

        @isolated_context_function
        @classmethod
        def class_method(cls) -> int:
            cv.set(4)
            return cv.get()

    foo = Foo()
    assert foo.static_method() == 3
    assert foo.class_method() == 4
    assert cv.get() == 5


@pytest.mark.asyncio
async def test_isolated_context_coroutine() -> None:
    cv: ContextVar[int] = ContextVar("cv")
    cv.set(1)

    @isolated_context_coroutine
    async def func() -> int:
        cv.set(2)
        return cv.get()

    assert await func() == 2
    assert cv.get() == 1

    cv.set(5)

    class Foo:
        @isolated_context_coroutine
        @staticmethod
        async def static_method() -> int:
            cv.set(3)
            return cv.get()

        @isolated_context_coroutine
        @classmethod
        async def class_method(cls) -> int:
            cv.set(4)
            return cv.get()

    foo = Foo()
    assert await foo.static_method() == 3
    assert await foo.class_method() == 4
    assert cv.get() == 5


def test_run_isolated() -> None:
    cv: ContextVar[int] = ContextVar("cv")
    cv.set(1)

    def func() -> int:
        cv.set(2)
        return cv.get()

    assert run_isolated(func) == 2
    assert cv.get() == 1


@pytest.mark.asyncio
async def test_async_run_isolated() -> None:
    cv: ContextVar[int] = ContextVar("cv")
    cv.set(1)

    async def func() -> int:
        cv.set(2)
        return cv.get()

    assert await async_run_isolated(func) == 2
    assert cv.get() == 1
