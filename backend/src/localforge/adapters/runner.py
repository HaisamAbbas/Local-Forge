"""Task isolation for asynchronous adapter calls and streaming responses."""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from localforge.adapters.contracts import AdapterStreamEvent

_StreamMessage = tuple[str, AdapterStreamEvent | Exception | None]


class AdapterCallRunner:
    """Run adapter awaits in tracked tasks so slow async calls yield to the API loop."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    async def invoke[ResultT](
        self,
        method: Callable[..., Coroutine[Any, Any, ResultT]],
        *args: object,
        **kwargs: object,
    ) -> ResultT:
        task: asyncio.Task[ResultT] = asyncio.create_task(method(*args, **kwargs))
        self._tasks.add(task)
        try:
            return await task
        finally:
            self._tasks.discard(task)

    async def stream(
        self,
        method: Callable[..., AsyncIterator[AdapterStreamEvent]],
        *args: object,
        **kwargs: object,
    ) -> AsyncIterator[AdapterStreamEvent]:
        """Pump an adapter iterator in its own task and cancel it on early close."""

        queue: asyncio.Queue[_StreamMessage] = asyncio.Queue(maxsize=32)

        async def produce() -> None:
            try:
                async for event in method(*args, **kwargs):
                    await queue.put(("event", event))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await queue.put(("error", error))
            await queue.put(("done", None))

        task = asyncio.create_task(produce())
        self._tasks.add(task)
        try:
            while True:
                message_type, payload = await queue.get()
                if message_type == "event":
                    assert isinstance(payload, AdapterStreamEvent)
                    yield payload
                elif message_type == "error":
                    assert isinstance(payload, Exception)
                    raise payload
                else:
                    return
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            finally:
                self._tasks.discard(task)

    async def close(self) -> None:
        """Cancel and await outstanding calls during application shutdown."""

        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
