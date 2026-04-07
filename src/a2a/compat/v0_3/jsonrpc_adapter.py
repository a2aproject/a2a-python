import logging

from collections.abc import AsyncIterable, AsyncIterator
from typing import TYPE_CHECKING, Any

from sse_starlette.sse import EventSourceResponse
from starlette.responses import JSONResponse


if TYPE_CHECKING:
    from starlette.requests import Request

    from a2a.server.request_handlers.request_handler import RequestHandler

    _package_starlette_installed = True
else:
    try:
        from starlette.requests import Request

        _package_starlette_installed = True
    except ImportError:
        Request = Any

        _package_starlette_installed = False

from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.request_handler import RequestHandler03
from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import (
    InternalError as CoreInternalError,
)
from a2a.server.jsonrpc_models import (
    InvalidRequestError as CoreInvalidRequestError,
)
from a2a.server.jsonrpc_models import (
    JSONRPCError as CoreJSONRPCError,
)
from a2a.server.routes.common import (
    DefaultServerCallContextBuilder,
    ServerCallContextBuilder,
)
from a2a.utils import constants
from a2a.utils.helpers import validate_version


logger = logging.getLogger(__name__)


class JSONRPC03Adapter:
    """Adapter to make RequestHandler work with v0.3 JSONRPC API."""

    METHOD_TO_MODEL = {
        'message/send': types_v03.SendMessageRequest,
        'message/stream': types_v03.SendStreamingMessageRequest,
        'tasks/get': types_v03.GetTaskRequest,
        'tasks/cancel': types_v03.CancelTaskRequest,
        'tasks/pushNotificationConfig/set': types_v03.SetTaskPushNotificationConfigRequest,
        'tasks/pushNotificationConfig/get': types_v03.GetTaskPushNotificationConfigRequest,
        'tasks/pushNotificationConfig/list': types_v03.ListTaskPushNotificationConfigRequest,
        'tasks/pushNotificationConfig/delete': types_v03.DeleteTaskPushNotificationConfigRequest,
        'tasks/resubscribe': types_v03.TaskResubscriptionRequest,
        'agent/getAuthenticatedExtendedCard': types_v03.GetAuthenticatedExtendedCardRequest,
    }

    def __init__(
        self,
        http_handler: 'RequestHandler',
        context_builder: 'ServerCallContextBuilder | None' = None,
    ):
        self.handler = RequestHandler03(
            request_handler=http_handler,
        )
        self._context_builder = (
            context_builder or DefaultServerCallContextBuilder()
        )

    def supports_method(self, method: str) -> bool:
        """Returns True if the v0.3 adapter supports the given method name."""
        return method in self.METHOD_TO_MODEL

    def _generate_error_response(
        self,
        request_id: 'str | int | None',
        error: 'Exception | CoreJSONRPCError',
    ) -> JSONResponse:
        if isinstance(error, CoreJSONRPCError):
            err_dict = error.model_dump(by_alias=True)
            return JSONResponse(
                {'jsonrpc': '2.0', 'id': request_id, 'error': err_dict}
            )

        internal_error = CoreInternalError(message=str(error))
        return JSONResponse(
            {
                'jsonrpc': '2.0',
                'id': request_id,
                'error': internal_error.model_dump(by_alias=True),
            }
        )

    async def handle_request(
        self,
        request_id: 'str | int | None',
        method: str,
        body: dict,
        request: Request,
    ) -> 'JSONResponse | EventSourceResponse':
        """Handles v0.3 specific JSON-RPC requests."""
        try:
            model_class = self.METHOD_TO_MODEL[method]
            try:
                specific_request = model_class.model_validate(body)  # type: ignore[attr-defined]
            except Exception as e:
                logger.exception(
                    'Failed to validate base JSON-RPC request for v0.3'
                )

                return self._generate_error_response(
                    request_id,
                    CoreInvalidRequestError(data=str(e)),
                )

            call_context = self._context_builder.build(request)
            call_context.tenant = (
                getattr(specific_request.params, 'tenant', '')
                if hasattr(specific_request, 'params')
                else getattr(specific_request, 'tenant', '')
            )
            call_context.state['method'] = method
            call_context.state['request_id'] = request_id

            if method in ('message/stream', 'tasks/resubscribe'):
                return await self._process_streaming_request(
                    request_id, specific_request, call_context
                )

            return await self._process_non_streaming_request(
                request_id, specific_request, call_context
            )
        except Exception as e:
            logger.exception('Unhandled exception in v0.3 JSONRPCAdapter')
            return self._generate_error_response(
                request_id, CoreInternalError(message=str(e))
            )

    @validate_version(constants.PROTOCOL_VERSION_0_3)
    async def _process_non_streaming_request(
        self,
        request_id: 'str | int | None',
        request_obj: Any,
        context: ServerCallContext,
    ) -> JSONResponse:
        method = request_obj.method
        result: Any
        if method == 'message/send':
            res_msg = await self.handler.on_message_send(request_obj, context)
            result = types_v03.SendMessageResponse(
                root=types_v03.SendMessageSuccessResponse(
                    id=request_id, result=res_msg
                )
            )
        elif method == 'tasks/get':
            res_get = await self.handler.on_get_task(request_obj, context)
            result = types_v03.GetTaskResponse(
                root=types_v03.GetTaskSuccessResponse(
                    id=request_id, result=res_get
                )
            )
        elif method == 'tasks/cancel':
            res_cancel = await self.handler.on_cancel_task(request_obj, context)
            result = types_v03.CancelTaskResponse(
                root=types_v03.CancelTaskSuccessResponse(
                    id=request_id, result=res_cancel
                )
            )
        elif method == 'tasks/pushNotificationConfig/get':
            res_get_push = (
                await self.handler.on_get_task_push_notification_config(
                    request_obj, context
                )
            )
            result = types_v03.GetTaskPushNotificationConfigResponse(
                root=types_v03.GetTaskPushNotificationConfigSuccessResponse(
                    id=request_id, result=res_get_push
                )
            )
        elif method == 'tasks/pushNotificationConfig/set':
            res_set_push = (
                await self.handler.on_create_task_push_notification_config(
                    request_obj, context
                )
            )
            result = types_v03.SetTaskPushNotificationConfigResponse(
                root=types_v03.SetTaskPushNotificationConfigSuccessResponse(
                    id=request_id, result=res_set_push
                )
            )
        elif method == 'tasks/pushNotificationConfig/list':
            res_list_push = (
                await self.handler.on_list_task_push_notification_configs(
                    request_obj, context
                )
            )
            result = types_v03.ListTaskPushNotificationConfigResponse(
                root=types_v03.ListTaskPushNotificationConfigSuccessResponse(
                    id=request_id, result=res_list_push
                )
            )
        elif method == 'tasks/pushNotificationConfig/delete':
            await self.handler.on_delete_task_push_notification_config(
                request_obj, context
            )
            result = types_v03.DeleteTaskPushNotificationConfigResponse(
                root=types_v03.DeleteTaskPushNotificationConfigSuccessResponse(
                    id=request_id, result=None
                )
            )
        elif method == 'agent/getAuthenticatedExtendedCard':
            res_card = await self.handler.on_get_extended_agent_card(
                request_obj, context
            )
            result = types_v03.GetAuthenticatedExtendedCardResponse(
                root=types_v03.GetAuthenticatedExtendedCardSuccessResponse(
                    id=request_id, result=res_card
                )
            )
        else:
            raise ValueError(f'Unsupported method {method}')

        return JSONResponse(
            content=result.model_dump(
                mode='json', by_alias=True, exclude_none=True
            )
        )

    @validate_version(constants.PROTOCOL_VERSION_0_3)
    async def _process_streaming_request(
        self,
        request_id: 'str | int | None',
        request_obj: Any,
        context: ServerCallContext,
    ) -> EventSourceResponse:
        method = request_obj.method
        if method == 'message/stream':
            stream_gen = self.handler.on_message_send_stream(
                request_obj, context
            )
        elif method == 'tasks/resubscribe':
            stream_gen = self.handler.on_subscribe_to_task(request_obj, context)
        else:
            raise ValueError(f'Unsupported streaming method {method}')

        async def event_generator(
            stream: AsyncIterable[Any],
        ) -> AsyncIterator[dict[str, str]]:
            try:
                async for item in stream:
                    yield {
                        'data': item.model_dump_json(
                            by_alias=True, exclude_none=True
                        )
                    }
            except Exception as e:
                logger.exception(
                    'Error during stream generation in v0.3 JSONRPCAdapter'
                )
                err = types_v03.InternalError(message=str(e))
                err_resp = types_v03.SendStreamingMessageResponse(
                    root=types_v03.JSONRPCErrorResponse(
                        id=request_id, error=err
                    )
                )
                yield {
                    'data': err_resp.model_dump_json(
                        by_alias=True, exclude_none=True
                    )
                }

        return EventSourceResponse(event_generator(stream_gen))
