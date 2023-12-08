from contextvars import ContextVar

import pytest
from configzen.copy_context import (
    copy_context_on_call,
    copy_context_on_await,
    copy_and_run,
    copy_and_await,
)

def test_copy_context_on_call() -> None:
    cv: ContextVar[int] = ContextVar("cv")
    cv.set(1)

    @copy_context_on_call
    def func() -> int:
        cv.set(2)
        return cv.get()

    assert func() == 2
    assert cv.get() == 1

    cv.set(5)

    class Foo:
        @copy_context_on_call
        @staticmethod
        def static_method() -> int:
            cv.set(3)
            return cv.get()

        @copy_context_on_call
        @classmethod
        def class_method(cls) -> int:
            cv.set(4)
            return cv.get()

    foo = Foo()
    assert foo.static_method() == 3
    assert foo.class_method() == 4
    assert cv.get() == 5


@pytest.mark.asyncio
async def test_copy_context_on_await() -> None:
    cv: ContextVar[int] = ContextVar("cv")
    cv.set(1)

    @copy_context_on_await
    async def func() -> int:
        cv.set(2)
        return cv.get()

    assert await func() == 2
    assert cv.get() == 1

    cv.set(5)

    class Foo:
        @copy_context_on_await
        @staticmethod
        async def static_method() -> int:
            cv.set(3)
            return cv.get()

        @copy_context_on_await
        @classmethod
        async def class_method(cls) -> int:
            cv.set(4)
            return cv.get()

    foo = Foo()
    assert await foo.static_method() == 3
    assert await foo.class_method() == 4
    assert cv.get() == 5


def test_copy_and_run() -> None:
    cv: ContextVar[int] = ContextVar("cv")
    cv.set(1)

    def func() -> int:
        cv.set(2)
        return cv.get()

    assert copy_and_run(func) == 2
    assert cv.get() == 1


@pytest.mark.asyncio
async def test_copy_and_await() -> None:
    cv: ContextVar[int] = ContextVar("cv")
    cv.set(1)

    async def func() -> int:
        cv.set(2)
        return cv.get()

    assert await copy_and_await(func) == 2
    assert cv.get() == 1
