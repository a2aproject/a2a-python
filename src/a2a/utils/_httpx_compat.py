"""Compatibility layer for `httpx` and `httpx2`.

Although both packages share the same API, their classes are distinct types, so
`httpx2` classes are included in type unions and exception catch-tuples when
`httpx2` is installed.
"""

from typing import TYPE_CHECKING

import httpx


AsyncClient = httpx.AsyncClient
Request = httpx.Request
Response = httpx.Response

if TYPE_CHECKING:
    import httpx2  # ty: ignore[unresolved-import]

    httpx_lib = httpx

    HTTPStatusError = httpx.HTTPStatusError | httpx2.HTTPStatusError

    HTTP_STATUS_ERROR: tuple[
        type[httpx.HTTPStatusError], type[httpx2.HTTPStatusError]
    ]
    REQUEST_ERROR: tuple[type[httpx.RequestError], type[httpx2.RequestError]]
    TIMEOUT_EXCEPTION: tuple[
        type[httpx.TimeoutException], type[httpx2.TimeoutException]
    ]
    STREAM_CLOSED: tuple[type[httpx.StreamClosed], type[httpx2.StreamClosed]]
    RESPONSE_NOT_READ: tuple[
        type[httpx.ResponseNotRead], type[httpx2.ResponseNotRead]
    ]
else:
    try:
        import httpx2

        httpx_lib = httpx2

        HTTPStatusError = httpx.HTTPStatusError | httpx2.HTTPStatusError

        HTTP_STATUS_ERROR = (
            httpx.HTTPStatusError,
            httpx2.HTTPStatusError,
        )
        REQUEST_ERROR = (
            httpx.RequestError,
            httpx2.RequestError,
        )
        TIMEOUT_EXCEPTION = (
            httpx.TimeoutException,
            httpx2.TimeoutException,
        )
        STREAM_CLOSED = (
            httpx.StreamClosed,
            httpx2.StreamClosed,
        )
        RESPONSE_NOT_READ = (
            httpx.ResponseNotRead,
            httpx2.ResponseNotRead,
        )
    except ImportError:
        httpx_lib = httpx

        HTTPStatusError = httpx.HTTPStatusError

        HTTP_STATUS_ERROR = (httpx.HTTPStatusError,)
        REQUEST_ERROR = (httpx.RequestError,)
        TIMEOUT_EXCEPTION = (httpx.TimeoutException,)
        STREAM_CLOSED = (httpx.StreamClosed,)
        RESPONSE_NOT_READ = (httpx.ResponseNotRead,)


__all__ = [
    'HTTP_STATUS_ERROR',
    'REQUEST_ERROR',
    'RESPONSE_NOT_READ',
    'STREAM_CLOSED',
    'TIMEOUT_EXCEPTION',
    'AsyncClient',
    'HTTPStatusError',
    'Request',
    'Response',
    'httpx_lib',
]
