import functools
import logging

from collections.abc import Awaitable, Callable, Coroutine
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


def rest_error_handler(
    func: Callable[..., Awaitable[Response]],
) -> Callable[..., Awaitable[Response]]:
    """Decorator to catch A2AError and map it to an appropriate JSONResponse."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Response:
        try:
            return await func(*args, **kwargs)
        except A2AError as error:
            mapping = A2A_REST_ERROR_MAPPING.get(
                type(error), RestErrorMap(500, 'INTERNAL', 'INTERNAL_ERROR')
            )
            http_code = mapping.http_code
            grpc_status = mapping.grpc_status
            reason = mapping.reason

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

            # SECURITY WARNING: Data attached to A2AError.data is serialized unaltered and exposed publicly to the client in the REST API response.
            metadata = getattr(error, 'data', None) or {}

            return JSONResponse(
                content=_build_error_payload(
                    code=http_code,
                    status=grpc_status,
                    message=getattr(error, 'message', str(error)),
                    reason=reason,
                    metadata=metadata,
                ),
                status_code=http_code,
                media_type='application/json',
            )
        except ParseError as error:
            logger.warning('Parse error: %s', str(error))
            return JSONResponse(
                content=_build_error_payload(
                    code=400,
                    status='INVALID_ARGUMENT',
                    message=str(error),
                    reason='INVALID_REQUEST',
                    metadata={},
                ),
                status_code=400,
                media_type='application/json',
            )
        except Exception:
            logger.exception('Unknown error occurred')
            return JSONResponse(
                content=_build_error_payload(
                    code=500,
                    status='INTERNAL',
                    message='unknown exception',
                ),
                status_code=500,
                media_type='application/json',
            )

    return wrapper


def rest_stream_error_handler(
    func: Callable[..., Coroutine[Any, Any, Any]],
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Decorator to catch A2AError for a streaming method, log it and then rethrow it to be handled by framework."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except A2AError as error:
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
            # Since the stream has started, we can't return a JSONResponse.
            # Instead, we run the error handling logic (provides logging)
            # and reraise the error and let server framework manage
            raise error
        except Exception as e:
            # Since the stream has started, we can't return a JSONResponse.
            # Instead, we run the error handling logic (provides logging)
            # and reraise the error and let server framework manage
            raise e

    return wrapper
