import collections.abc
from typing import AsyncGenerator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class PrintingAsyncGenerator(collections.abc.AsyncGenerator):
    """
    Wraps an async generator to print items as they are yielded,
    fully supporting bi-directional flow (asend, athrow, aclose).
    """

    def __init__(self, url: str, ag: AsyncGenerator):
        self.url = url
        self._ag = ag

    async def asend(self, value):
        # Forward the sent value to the underlying async generator
        result = await self._ag.asend(value)
        print(f'PrintingAsyncGenerator::Generated: {self.url} {result}')
        return result

    async def athrow(self, typ, val=None, tb=None):
        # Forward exceptions to the underlying async generator
        result = await self._ag.athrow(typ, val, tb)
        print(
            f'PrintingAsyncGenerator::Generated (via athrow): {self.url} {result}'
        )
        return result

    async def aclose(self):
        # Gracefully shut down the underlying generator
        await self._ag.aclose()


class CustomLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        print('-' * 80)
        print(f'REQUEST: {request.method} {request.url}')
        print(f'REQUEST BODY: {await request.body()}')

        response = await call_next(request)
        # Disabled by default. Can hang the test if enabled.
        # response.body_iterator = PrintingAsyncGenerator(request.url, response.body_iterator)

        print('-' * 80)
        return response
