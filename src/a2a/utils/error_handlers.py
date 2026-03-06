import functools
import logging

from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from starlette.responses import JSONResponse, Response
else:
    try:
        from starlette.responses import JSONResponse, Response
    except ImportError:
        JSONResponse = Any
        Response = Any


from a2a.server.jsonrpc_models import (
    InternalError as JSONRPCInternalError,
)
from a2a.server.jsonrpc_models import (
    JSONParseError,
    JSONRPCError,
)
from a2a.utils.errors import (
    A2AError,
    AuthenticatedExtendedCardNotConfiguredError,
    ContentTypeNotSupportedError,
    InternalError,
    InvalidAgentResponseError,
    InvalidParamsError,
    InvalidRequestError,
    MethodNotFoundError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)


logger = logging.getLogger(__name__)

_A2AErrorType = (
    type[JSONRPCError]
    | type[JSONParseError]
    | type[InvalidRequestError]
    | type[MethodNotFoundError]
    | type[InvalidParamsError]
    | type[InternalError]
    | type[JSONRPCInternalError]
    | type[TaskNotFoundError]
    | type[TaskNotCancelableError]
    | type[PushNotificationNotSupportedError]
    | type[UnsupportedOperationError]
    | type[ContentTypeNotSupportedError]
    | type[InvalidAgentResponseError]
    | type[AuthenticatedExtendedCardNotConfiguredError]
)

A2AErrorToHttpStatus: dict[_A2AErrorType, int] = {
    JSONRPCError: 500,
    JSONParseError: 400,
    InvalidRequestError: 400,
    MethodNotFoundError: 404,
    InvalidParamsError: 400,
    InternalError: 500,
    JSONRPCInternalError: 500,
    TaskNotFoundError: 404,
    TaskNotCancelableError: 409,
    PushNotificationNotSupportedError: 400,
    UnsupportedOperationError: 400,
    ContentTypeNotSupportedError: 415,
    InvalidAgentResponseError: 502,
    AuthenticatedExtendedCardNotConfiguredError: 400,
}

A2AErrorToTypeURI: dict[_A2AErrorType, str] = {
    TaskNotFoundError: 'https://a2a-protocol.org/errors/task-not-found',
    TaskNotCancelableError: 'https://a2a-protocol.org/errors/task-not-cancelable',
    PushNotificationNotSupportedError: 'https://a2a-protocol.org/errors/push-notification-not-supported',
    UnsupportedOperationError: 'https://a2a-protocol.org/errors/unsupported-operation',
    ContentTypeNotSupportedError: 'https://a2a-protocol.org/errors/content-type-not-supported',
    InvalidAgentResponseError: 'https://a2a-protocol.org/errors/invalid-agent-response',
    AuthenticatedExtendedCardNotConfiguredError: 'https://a2a-protocol.org/errors/extended-agent-card-not-configured',
}

A2AErrorToTitle: dict[_A2AErrorType, str] = {
    JSONRPCError: 'JSON RPC Error',
    JSONParseError: 'JSON Parse Error',
    InvalidRequestError: 'Invalid Request Error',
    MethodNotFoundError: 'Method Not Found Error',
    InvalidParamsError: 'Invalid Params Error',
    InternalError: 'Internal Error',
    JSONRPCInternalError: 'Internal Error',
    TaskNotFoundError: 'Task Not Found',
    TaskNotCancelableError: 'Task Not Cancelable',
    PushNotificationNotSupportedError: 'Push Notification Not Supported',
    UnsupportedOperationError: 'Unsupported Operation',
    ContentTypeNotSupportedError: 'Content Type Not Supported',
    InvalidAgentResponseError: 'Invalid Agent Response',
    AuthenticatedExtendedCardNotConfiguredError: 'Extended Agent Card Not Configured',
}


def _build_problem_details_response(error: A2AError) -> JSONResponse:
    """Helper to convert exceptions to RFC 9457 Problem Details responses."""
    error_type = cast('_A2AErrorType', type(error))
    http_code = A2AErrorToHttpStatus.get(error_type, 500)
    type_uri = A2AErrorToTypeURI.get(error_type, 'about:blank')
    title = A2AErrorToTitle.get(error_type, error.__class__.__name__)

    log_level = (
        logging.ERROR if isinstance(error, InternalError) else logging.WARNING
    )
    logger.log(
        log_level,
        "Request error: Code=%s, Message='%s'%s",
        getattr(error, 'code', 'N/A'),
        getattr(error, 'message', str(error)),
        ', Data=' + str(getattr(error, 'data', ''))
        if getattr(error, 'data', None)
        else '',
    )

    payload = {
        'type': type_uri,
        'title': title,
        'status': http_code,
        'detail': getattr(error, 'message', str(error)),
    }

    data = getattr(error, 'data', None)
    if isinstance(data, dict):
        for key, value in data.items():
            if key not in payload:
                payload[key] = value

    return JSONResponse(
        content=payload,
        status_code=http_code,
        media_type='application/problem+json',
    )


def rest_error_handler(
    func: Callable[..., Awaitable[Response]],
) -> Callable[..., Awaitable[Response]]:
    """Decorator to catch A2AError and map it to an appropriate JSONResponse."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Response:
        try:
            return await func(*args, **kwargs)
        except A2AError as error:
            return _build_problem_details_response(error)
        except Exception:
            logger.exception('Unknown error occurred')
            return JSONResponse(
                content={
                    'type': 'about:blank',
                    'title': 'Internal Error',
                    'status': 500,
                    'detail': 'Unknown exception',
                },
                status_code=500,
                media_type='application/problem+json',
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
                ', Data=' + str(getattr(error, 'data', ''))
                if getattr(error, 'data', None)
                else '',
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
