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


from a2a._base import A2ABaseModel
from a2a.types import (
    AuthenticatedExtendedCardNotConfiguredError,
    ContentTypeNotSupportedError,
    InternalError,
    InvalidAgentResponseError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    MethodNotFoundError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.errors import ServerError


logger = logging.getLogger(__name__)

A2AErrorToHttpStatus: dict[type[A2ABaseModel], int] = {
    JSONParseError: 400,
    InvalidRequestError: 400,
    MethodNotFoundError: 404,
    InvalidParamsError: 422,
    InternalError: 500,
    TaskNotFoundError: 404,
    TaskNotCancelableError: 409,
    PushNotificationNotSupportedError: 501,
    UnsupportedOperationError: 501,
    ContentTypeNotSupportedError: 415,
    InvalidAgentResponseError: 502,
    AuthenticatedExtendedCardNotConfiguredError: 404,
}


def rest_error_handler(
    func: Callable[..., Awaitable[Response]],
) -> Callable[..., Awaitable[Response]]:
    """Decorator to catch ServerError and map it to an appropriate JSONResponse."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Response:
        try:
            return await func(*args, **kwargs)
        except ServerError as e:
            error = e.error or InternalError(
                message='Internal error due to unknown reason'
            )
            http_code = A2AErrorToHttpStatus.get(type(error), 500)

            log_level = (
                logging.ERROR
                if isinstance(error, InternalError)
                else logging.WARNING
            )
            logger.log(
                log_level,
                "Request error: Code=%s, Message='%s'%s",
                error.code,
                error.message,
                ', Data=' + str(error.data) if error.data else '',
            )
            return JSONResponse(
                content={'message': error.message}, status_code=http_code
            )
        except Exception:
            logger.exception('Unknown error occurred')
            return JSONResponse(
                content={'message': 'unknown exception'}, status_code=500
            )

    return wrapper


def rest_stream_error_handler(
    func: Callable[..., Coroutine[Any, Any, Any]],
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Decorator to catch ServerError for a streaming method,log it and then rethrow it to be handled by framework."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except ServerError as e:
            error = e.error or InternalError(
                message='Internal error due to unknown reason'
            )

            log_level = (
                logging.ERROR
                if isinstance(error, InternalError)
                else logging.WARNING
            )
            logger.log(
                log_level,
                "Request error: Code=%s, Message='%s'%s",
                error.code,
                error.message,
                ', Data=' + str(error.data) if error.data else '',
            )
            # Since the stream has started, we can't return a JSONResponse.
            # Instead, we run the error handling logic (provides logging)
            # and reraise the error and let server framework manage
            raise e
        except Exception as e:
            # Since the stream has started, we can't return a JSONResponse.
            # Instead, we run the error handling logic (provides logging)
            # and reraise the error and let server framework manage
            raise e

    return wrapper
