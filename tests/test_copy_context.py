from contextvars import ContextVar

import pytest
from configzen.copy_context import (
    copy_context_on_call,
    copy_context_on_await,
    copy_and_run,
    copy_and_await,
)

def test_copy_context_on_call() -> None:
    cv = ContextVar("cv")
    cv.set(1)

    @copy_context_on_call
    def func() -> int:
        cv.set(2)
        return cv.get()

    assert func() == 2
    assert cv.get() == 1


@pytest.mark.asyncio
async def test_copy_context_on_await() -> None:
    cv = ContextVar("cv")
    cv.set(1)

    @copy_context_on_await
    async def func() -> int:
        cv.set(2)
        return cv.get()

    assert await func() == 2
    assert cv.get() == 1


def test_copy_and_run() -> None:
    cv = ContextVar("cv")
    cv.set(1)

    def func() -> int:
        cv.set(2)
        return cv.get()

    assert copy_and_run(func) == 2
    assert cv.get() == 1


@pytest.mark.asyncio
async def test_copy_and_await() -> None:
    cv = ContextVar("cv")
    cv.set(1)

    async def func() -> int:
        cv.set(2)
        return cv.get()

    assert await copy_and_await(func) == 2
    assert cv.get() == 1
