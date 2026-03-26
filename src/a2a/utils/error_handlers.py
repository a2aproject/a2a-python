import functools
import inspect
import logging

from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from starlette.responses import JSONResponse, Response
else:
    try:
        from starlette.responses import JSONResponse, Response
    except ImportError:
        JSONResponse = Any
        Response = Any


from google.protobuf.json_format import ParseError

from a2a.utils.errors import (
    A2A_REST_ERROR_MAPPING,
    A2AError,
    InternalError,
    RestErrorMap,
)


logger = logging.getLogger(__name__)


def _build_error_payload(
    code: int,
    status: str,
    message: str,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Helper function to build the JSON error payload."""
    payload: dict[str, Any] = {
        'code': code,
        'status': status,
        'message': message,
    }
    if reason:
        payload['details'] = [
            {
                '@type': 'type.googleapis.com/google.rpc.ErrorInfo',
                'reason': reason,
                'domain': 'a2a-protocol.org',
                'metadata': metadata if metadata is not None else {},
            }
        ]
    return {'error': payload}


def build_rest_error_payload(error: Exception) -> dict[str, Any]:
    """Build a REST error payload dict from an exception.

    Returns:
        A dict with the error payload in the standard REST error format.
    """
    if isinstance(error, A2AError):
        mapping = A2A_REST_ERROR_MAPPING.get(
            type(error), RestErrorMap(500, 'INTERNAL', 'INTERNAL_ERROR')
        )
        # SECURITY WARNING: Data attached to A2AError.data is serialized unaltered and exposed publicly to the client in the REST API response.
        metadata = getattr(error, 'data', None) or {}
        return _build_error_payload(
            code=mapping.http_code,
            status=mapping.grpc_status,
            message=getattr(error, 'message', str(error)),
            reason=mapping.reason,
            metadata=metadata,
        )
    if isinstance(error, ParseError):
        return _build_error_payload(
            code=400,
            status='INVALID_ARGUMENT',
            message=str(error),
            reason='INVALID_REQUEST',
            metadata={},
        )
    return _build_error_payload(
        code=500,
        status='INTERNAL',
        message='unknown exception',
    )


def _create_error_response(error: Exception) -> Response:
    """Helper function to create a JSONResponse for an error."""
    if isinstance(error, A2AError):
        log_level = (
            logging.ERROR
            if isinstance(error, InternalError)
            else logging.WARNING
        )
        logger.log(
            log_level,
            "Request error: Code=%s, Message='%s'%s",
            getattr(error, 'code', 'N/A'),
            getattr(error, 'message', str(error)),
            f', Data={error.data}' if error.data else '',
        )
    elif isinstance(error, ParseError):
        logger.warning('Parse error: %s', str(error))
    else:
        logger.exception('Unknown error occurred')

    payload = build_rest_error_payload(error)
    # Extract HTTP status code from the payload
    http_code = payload.get('error', {}).get('code', 500)
    return JSONResponse(
        content=payload,
        status_code=http_code,
        media_type='application/json',
    )


def rest_error_handler(
    func: Callable[..., Awaitable[Response]],
) -> Callable[..., Awaitable[Response]]:
    """Decorator to catch A2AError and map it to an appropriate JSONResponse."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Response:
        try:
            return await func(*args, **kwargs)
        except Exception as error:  # noqa: BLE001
            return _create_error_response(error)

    return wrapper


def rest_stream_error_handler(
    func: Callable[..., Coroutine[Any, Any, Any]],
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Decorator to catch A2AError for a streaming method. Maps synchronous errors to JSONResponse and logs streaming errors."""

    def _log_error(error: Exception) -> None:
        if isinstance(error, A2AError):
            log_level = (
                logging.ERROR
                if isinstance(error, InternalError)
                else logging.WARNING
            )
            logger.log(
                log_level,
                "Request error: Code=%s, Message='%s'%s",
                getattr(error, 'code', 'N/A'),
                getattr(error, 'message', str(error)),
                f', Data={error.data}' if error.data else '',
            )
        else:
            logger.exception('Unknown streaming error occurred')

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            response = await func(*args, **kwargs)

            # If the response has an async generator body (like EventSourceResponse),
            # we must wrap it to catch errors that occur during stream execution.
            if hasattr(response, 'body_iterator') and inspect.isasyncgen(
                response.body_iterator
            ):
                original_iterator = response.body_iterator

                async def error_catching_iterator() -> AsyncGenerator[
                    Any, None
                ]:
                    try:
                        async for item in original_iterator:
                            yield item
                    except Exception as stream_error:
                        _log_error(stream_error)
                        raise stream_error

                response.body_iterator = error_catching_iterator()

        except Exception as e:  # noqa: BLE001
            return _create_error_response(e)
        else:
            return response

    return wrapper
