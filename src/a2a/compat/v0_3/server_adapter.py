import json
import asyncio
from fastapi.responses import JSONResponse, StreamingResponse
import logging
from collections.abc import AsyncGenerator, Awaitable
from typing import Any

from google.protobuf.json_format import MessageToDict
from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response

from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import JSONRPCError, InvalidParamsError
from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.conversions import (
    to_core_send_message_request,
    to_compat_send_message_response,
    to_compat_stream_response,
    to_core_get_task_request,
    to_compat_task,
    to_core_cancel_task_request,
    to_core_create_task_push_notification_config_request,
    to_compat_task_push_notification_config,
    to_core_get_task_push_notification_config_request,
    to_core_delete_task_push_notification_config_request,
    to_core_list_task_push_notification_config_request,
    to_compat_list_task_push_notification_config_response,
    to_core_subscribe_to_task_request,
    to_core_get_extended_agent_card_request,
    to_compat_agent_card,
)

logger = logging.getLogger(__name__)

LEGACY_JSONRPC_METHODS = {
    'message/send',
    'message/stream',
    'tasks/get',
    'tasks/cancel',
    'tasks/pushNotificationConfig/set',
    'tasks/pushNotificationConfig/get',
    'tasks/pushNotificationConfig/delete',
    'tasks/pushNotificationConfig/list',
    'tasks/resubscribe',
    'agent/getAuthenticatedExtendedCard',
}

async def handle_jsonrpc_legacy_request(
    app: Any,
    method: str,
    base_request: JSONRPC20Request,
    request: Any,  # Starlette Request
    context_builder: Any,
) -> Any | None:
    """
    Intercepts legacy v0.3 JSON-RPC requests, processes them using the v1.0 handler,
    and returns a properly formatted legacy Starlette Response.
    Returns None if the method is not a legacy method.
    """
    if method not in LEGACY_JSONRPC_METHODS:
        return None

    request_id = base_request._id
    
    # 1) Build context
    call_context = context_builder.build(request)
    call_context.state['method'] = method
    call_context.state['request_id'] = request_id

    params = base_request.data.get('params', {})
    
    try:
        # Pre-process params to undo legacy specific structures before Pydantic parsing if necessary
        # Usually Pydantic parsing handles it, but we might need to inject 'kind' or adjust roles.
        
        # Actually we use our `types_v03` Pydantic models directly which perfectly map legacy JSON.
        pass
    except Exception as e:
        logger.exception("Failed to pre-process legacy request params")
        return app._generate_error_response(request_id, InvalidParamsError(data=str(e)))

    # 2) Dispatch based on method
    try:
        is_legacy = False
        if 'message' in params:
            msg = params['message']
            if 'content' in msg or ('role' in msg and msg['role'] in ['user', 'agent']):
                is_legacy = True
        elif method in ['tasks/get', 'tasks/cancel', 'tasks/resubscribe']:
            is_legacy = True
            
        if not is_legacy:
            return None
            
        if method == 'message/send':
            req_v03 = types_v03.SendMessageRequest(jsonrpc="2.0", method=method, params=params, id=request_id)
            req_v10 = to_core_send_message_request(req_v03)
            handler_result = await app.handler.on_message_send(req_v10, call_context)
            # handler_result is a dict: {"jsonrpc": "2.0", "result": {"task": ...}, "id": ...}
            # We need to extract the pb2_v10 Task, convert it, and build legacy dict
            from a2a.types.a2a_pb2 import SendMessageResponse
            from google.protobuf.json_format import ParseDict
            # The easiest way is to intercept what the handler produced
            if "error" in handler_result:
                return app._create_response(call_context, handler_result)
            
            # Since we know the handler builds success response containing 'result' which is a dict of SendMessageResponse
            res_v10 = ParseDict(handler_result["result"], SendMessageResponse())
            res_v03_model = to_compat_send_message_response(res_v10)
            # For sync message/send in v0.3, the result is the object itself (Task or Message), 
            # not a wrapper with 'task' or 'message' key.
            # to_compat_send_message_response returns a SendMessageResponse RootModel
            # which has the SendMessageSuccessResponse in .root.
            from a2a.compat.v0_3.types import SendMessageSuccessResponse
            if isinstance(res_v03_model.root, SendMessageSuccessResponse):
                 legacy_result = res_v03_model.root.result.model_dump(exclude_none=True, by_alias=True)
            else:
                 # Error response
                 legacy_result = res_v03_model.root.model_dump(exclude_none=True, by_alias=True)
            
            return app._create_response(call_context, {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": legacy_result
            })
            
        elif method == 'message/stream':
            req_v03 = types_v03.SendStreamingMessageRequest(jsonrpc="2.0", method=method, params=params, id=request_id)
            req_v10 = to_core_send_message_request(req_v03) # type: ignore
            
            async def legacy_stream_generator():
                stream = app.handler.on_message_send_stream(req_v10, call_context)
                async for item in stream:
                    if isinstance(item, dict) and "error" in item:
                        yield f"data: {json.dumps(item)}\n\n"
                        continue
                    
                    from a2a.types.a2a_pb2 import StreamResponse
                    from google.protobuf.json_format import ParseDict
                    from a2a.utils import proto_utils
                    
                    if hasattr(item, 'SerializeToString'):
                        res_v10 = proto_utils.to_stream_response(item)
                    elif isinstance(item, dict):
                        if "result" in item:
                             res_v10 = ParseDict(item["result"], StreamResponse(), ignore_unknown_fields=True)
                        else:
                             res_v10 = ParseDict(item, StreamResponse(), ignore_unknown_fields=True)
                    else:
                        raise ValueError(f"Unknown item type in stream: {type(item)}")
                        
                    res_v03_model = to_compat_stream_response(res_v10)
                    data_json = json.dumps({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": res_v03_model.result.model_dump(exclude_none=True, by_alias=True)
                    })
                    yield f"data: {data_json}\n\n"
                    
            return StreamingResponse(legacy_stream_generator(), media_type="text/event-stream")

        elif method == 'tasks/get':
            req_v03 = types_v03.GetTaskRequest(jsonrpc="2.0", method=method, params=params, id=request_id)
            req_v10 = to_core_get_task_request(req_v03)
            handler_result = await app.handler.on_get_task(req_v10, call_context)
            if "error" in handler_result: return app._create_response(call_context, handler_result)
            
            from a2a.types.a2a_pb2 import Task
            from google.protobuf.json_format import ParseDict
            res_v10 = ParseDict(handler_result["result"], Task())
            legacy_result = to_compat_task(res_v10).model_dump(exclude_none=True, by_alias=True)
            return app._create_response(call_context, {"jsonrpc": "2.0", "id": request_id, "result": legacy_result})

        elif method == 'tasks/cancel':
            req_v03 = types_v03.CancelTaskRequest(jsonrpc="2.0", method=method, params=params, id=request_id)
            req_v10 = to_core_cancel_task_request(req_v03)
            handler_result = await app.handler.on_cancel_task(req_v10, call_context)
            if "error" in handler_result: return app._create_response(call_context, handler_result)
            
            from a2a.types.a2a_pb2 import Task
            from google.protobuf.json_format import ParseDict
            res_v10 = ParseDict(handler_result["result"], Task())
            legacy_result = to_compat_task(res_v10).model_dump(exclude_none=True, by_alias=True)
            return app._create_response(call_context, {"jsonrpc": "2.0", "id": request_id, "result": legacy_result})

        elif method == 'tasks/resubscribe':
            # v0.3 SubscribeToTask equivalent
            req_v03 = types_v03.TaskResubscriptionRequest(jsonrpc="2.0", method=method, params=params, id=request_id)
            req_v10 = to_core_subscribe_to_task_request(req_v03)
            
            async def legacy_stream_generator():
                try:
                    stream = app.handler.on_subscribe_to_task(req_v10, call_context)
                    async for item in stream:
                        if isinstance(item, dict) and "error" in item:
                            yield f"data: {json.dumps(item)}\n\n"
                            continue
                        
                        from a2a.types.a2a_pb2 import StreamResponse
                        from google.protobuf.json_format import ParseDict
                        from a2a.utils import proto_utils
                        
                        if hasattr(item, 'SerializeToString'):
                             res_v10 = proto_utils.to_stream_response(item)
                        elif isinstance(item, dict):
                             if "result" in item:
                                  res_v10 = ParseDict(item["result"], StreamResponse(), ignore_unknown_fields=True)
                             else:
                                  res_v10 = ParseDict(item, StreamResponse(), ignore_unknown_fields=True)
                        else:
                             raise ValueError(f"Unknown item type in stream: {type(item)}")
                             
                        res_v03_model = to_compat_stream_response(res_v10)
                        data_json = json.dumps({
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": res_v03_model.result.model_dump(exclude_none=True, by_alias=True)
                        })
                        yield f"data: {data_json}\n\n"
                except Exception as e:
                    # If terminal, just yield the task once
                    if 'terminal state' in str(e).lower():
                        from a2a.types.a2a_pb2 import GetTaskRequest
                        try:
                            task = await app.handler.on_get_task(GetTaskRequest(id=req_v10.id), call_context)
                            res_v03 = to_compat_task(task)
                            data_json = json.dumps({
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "result": res_v03.model_dump(exclude_none=True, by_alias=True)
                            })
                            yield f"data: {data_json}\n\n"
                        except Exception:
                            pass
                    else:
                        logger.exception("Error in legacy stream generator")
                    
            return StreamingResponse(legacy_stream_generator(), media_type="text/event-stream")

        elif method == 'agent/getAuthenticatedExtendedCard':
            req_v03 = types_v03.GetAgentCardRequest(jsonrpc="2.0", method=method, params=params, id=request_id)
            req_v10 = to_core_get_extended_agent_card_request(req_v03)
            handler_result = await app.handler.get_authenticated_extended_card(req_v10, call_context)
            if "error" in handler_result: return app._create_response(call_context, handler_result)
            
            from a2a.types.a2a_pb2 import AgentCard
            from google.protobuf.json_format import ParseDict
            res_v10 = ParseDict(handler_result["result"], AgentCard())
            legacy_result = to_compat_agent_card(res_v10).model_dump(exclude_none=True, by_alias=True)
            return app._create_response(call_context, {"jsonrpc": "2.0", "id": request_id, "result": legacy_result})

        elif method == 'tasks/pushNotificationConfig/set':
            req_v03 = types_v03.CreateTaskPushNotificationConfigRequest(jsonrpc="2.0", method=method, params=params, id=request_id)
            req_v10 = to_core_create_task_push_notification_config_request(req_v03)
            handler_result = await app.handler.set_push_notification_config(req_v10, call_context)
            if "error" in handler_result: return app._create_response(call_context, handler_result)
            
            from a2a.types.a2a_pb2 import TaskPushNotificationConfig
            from google.protobuf.json_format import ParseDict
            res_v10 = ParseDict(handler_result["result"], TaskPushNotificationConfig())
            legacy_result = to_compat_task_push_notification_config(res_v10).model_dump(exclude_none=True, by_alias=True)
            return app._create_response(call_context, {"jsonrpc": "2.0", "id": request_id, "result": legacy_result})

        elif method == 'tasks/pushNotificationConfig/get':
            req_v03 = types_v03.GetTaskPushNotificationConfigRequest(jsonrpc="2.0", method=method, params=params, id=request_id)
            req_v10 = to_core_get_task_push_notification_config_request(req_v03)
            handler_result = await app.handler.get_push_notification_config(req_v10, call_context)
            if "error" in handler_result: return app._create_response(call_context, handler_result)
            
            from a2a.types.a2a_pb2 import TaskPushNotificationConfig
            from google.protobuf.json_format import ParseDict
            res_v10 = ParseDict(handler_result["result"], TaskPushNotificationConfig())
            legacy_result = to_compat_task_push_notification_config(res_v10).model_dump(exclude_none=True, by_alias=True)
            return app._create_response(call_context, {"jsonrpc": "2.0", "id": request_id, "result": legacy_result})

        elif method == 'tasks/pushNotificationConfig/delete':
            req_v03 = types_v03.DeleteTaskPushNotificationConfigRequest(jsonrpc="2.0", method=method, params=params, id=request_id)
            req_v10 = to_core_delete_task_push_notification_config_request(req_v03)
            handler_result = await app.handler.delete_push_notification_config(req_v10, call_context)
            if "error" in handler_result: return app._create_response(call_context, handler_result)
            
            return app._create_response(call_context, {"jsonrpc": "2.0", "id": request_id, "result": {}})

        elif method == 'tasks/pushNotificationConfig/list':
            req_v03 = types_v03.ListTaskPushNotificationConfigRequest(jsonrpc="2.0", method=method, params=params, id=request_id)
            req_v10 = to_core_list_task_push_notification_config_request(req_v03)
            handler_result = await app.handler.list_push_notification_configs(req_v10, call_context)
            if "error" in handler_result: return app._create_response(call_context, handler_result)
            
            from a2a.types.a2a_pb2 import ListTaskPushNotificationConfigsResponse
            from google.protobuf.json_format import ParseDict
            res_v10 = ParseDict(handler_result["result"], ListTaskPushNotificationConfigsResponse())
            legacy_result = to_compat_list_task_push_notification_config_response(res_v10).model_dump(exclude_none=True, by_alias=True)
            return app._create_response(call_context, {"jsonrpc": "2.0", "id": request_id, "result": legacy_result})
            
        # Add other methods here...
        
    except Exception as e:
        logger.exception('Unhandled exception in legacy JSON-RPC handler')
        from a2a.server.jsonrpc_models import InternalError
        return app._generate_error_response(request_id, InternalError(message=str(e)))

    return None

from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.apps.rest.fastapi_app import A2ARESTFastAPIApplication
from starlette.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from a2a.server.context import ServerCallContext
from fastapi import Request
from collections.abc import Callable
import functools

class Compat03RESTAdapter(RESTAdapter):
    """
    Extends RESTAdapter to inspect incoming requests. If they look like v0.3 requests
    (e.g., using 'content' instead of 'parts'), it rewrites the body and ensures
    the response is formatted as v0.3.
    """
    
    async def _handle_request(self, method: Callable, request: Request) -> JSONResponse:
        from packaging.version import Version, InvalidVersion
        body = await request.body()
        is_legacy_request = True
        
        version_header = request.headers.get("X-A2A-Version")
        if version_header:
             try:
                 if Version(version_header) >= Version('1.0.0'):
                     is_legacy_request = False
             except InvalidVersion:
                 pass
                 
        if is_legacy_request and b'"content"' in body:
            body_str = body.decode("utf-8")
            
            # Use json parsing for more reliable injection of defaults
            try:
                data = json.loads(body_str)
                # Ensure blocking is true for v0.3 requests if not explicitly false
                if 'configuration' not in data or data['configuration'] is None:
                    data['configuration'] = {'blocking': True}
                elif 'blocking' not in data['configuration'] or data['configuration']['blocking'] is None:
                    data['configuration']['blocking'] = True
                
                body_str = json.dumps(data)
            except Exception:
                pass

            body_str = body_str.replace('"content"', '"parts"')
            body_str = body_str.replace('"user"', '"ROLE_USER"').replace('"agent"', '"ROLE_AGENT"')
            async def new_receive():
                return {"type": "http.request", "body": body_str.encode("utf-8"), "more_body": False}
            request = Request(request.scope, new_receive)
            
        response = await super()._handle_request(method, request)
        
        # Always make the response backward compatible by injecting legacy fields
        try:
            data = json.loads(response.body)
            json_str = json.dumps(data)
            
            def inject_legacy_fields(obj):
                if isinstance(obj, dict):
                    if 'parts' in obj:
                        obj['content'] = obj.pop('parts')
                    if 'state' in obj and obj['state'] == 'TASK_STATE_CANCELED':
                        obj['state'] = 'TASK_STATE_CANCELLED'
                    for v in obj.values():
                        inject_legacy_fields(v)
                elif isinstance(obj, list):
                    for item in obj:
                        inject_legacy_fields(item)
                        
            if is_legacy_request:
                inject_legacy_fields(data)
                
                # Also ensure role/state strings are correct if they were mutated? 
                # Actually v0.3 SDK can handle lowercase if we use Pydantic, 
                # but for pure proto it needs upper.
                # Since REST v0.3 used proto JSON, we should probably stick to upper roles.
                # But our inject_legacy_fields doesn't do that yet.
                
                headers = dict(response.headers)
                headers.pop('content-length', None)
                headers.pop('Content-Length', None)
                return JSONResponse(content=data, headers=headers, status_code=response.status_code)
        except Exception as e:
            logger.error(f"Failed to rewrite legacy REST response: {e}")
            
        return response

    async def _handle_streaming_request(self, method: Callable, request: Request) -> EventSourceResponse:
        from packaging.version import Version, InvalidVersion
        body = await request.body()
        is_legacy_request = True
        
        version_header = request.headers.get("X-A2A-Version")
        if version_header:
             try:
                 if Version(version_header) >= Version('1.0.0'):
                     is_legacy_request = False
             except InvalidVersion:
                 pass
                 
        if is_legacy_request and b'"content"' in body:
            body_str = body.decode("utf-8")
            body_str = body_str.replace('"content"', '"parts"')
            async def new_receive():
                return {"type": "http.request", "body": body_str.encode("utf-8"), "more_body": False}
            request = Request(request.scope, new_receive)
            
        call_context = self._context_builder.build(request)

        async def event_generator(stream_coro):
            try:
                stream = await stream_coro if asyncio.iscoroutine(stream_coro) else stream_coro
                async for item in stream:
                    try:
                        data_obj = json.loads(item) if isinstance(item, str) else item
                        # If item is pb2 Message, convert to dict
                        if hasattr(data_obj, 'ListFields'):
                             from google.protobuf.json_format import MessageToDict
                             data_obj = MessageToDict(data_obj)
                        
                        def inject_legacy_fields(obj):
                            if isinstance(obj, dict):
                                if 'parts' in obj:
                                    obj['content'] = obj.pop('parts')
                                if 'state' in obj and obj['state'] == 'TASK_STATE_CANCELED':
                                    obj['state'] = 'TASK_STATE_CANCELLED'
                                for v in obj.values():
                                    inject_legacy_fields(v)
                            elif isinstance(obj, list):
                                for x in obj:
                                    inject_legacy_fields(x)
                        
                        if is_legacy_request:
                            inject_legacy_fields(data_obj)
                        yield {'data': json.dumps(data_obj)}
                    except Exception as e:
                        logger.error(f"Failed to rewrite legacy REST stream response: {e}")
                        yield {'data': item if isinstance(item, str) else json.dumps(item)}
            except Exception as e:
                if 'terminal state' in str(e).lower():
                    # Yield terminal task
                    try:
                        # Extract task_id from request path if possible, or assume we know it?
                        # This is tricky for generic handler.
                        pass
                    except Exception:
                         pass
                else:
                    logger.exception("Error in REST legacy stream generator")

        # Wait to prevent Starlette issues
        try:
            await request.body()
        except Exception:
            pass

        return EventSourceResponse(
            event_generator(method(request, call_context))
        )

class Compat03RESTFastAPIApplication(A2ARESTFastAPIApplication):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Re-initialize adapter
        from a2a.server.apps.rest.rest_adapter import RESTAdapter
        
        self._adapter = Compat03RESTAdapter(
            agent_card=kwargs.get("agent_card"),
            http_handler=kwargs.get("http_handler"),
            extended_agent_card=kwargs.get("extended_agent_card"),
            context_builder=kwargs.get("context_builder"),
            card_modifier=kwargs.get("card_modifier"),
            extended_card_modifier=kwargs.get("extended_card_modifier"),
        )
