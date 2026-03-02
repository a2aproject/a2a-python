import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any, cast
import grpc
import json
from uuid import uuid4
from httpx_sse import aconnect_sse
from google.protobuf import json_format
from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response

from a2a.client.middleware import ClientCallContext
from a2a.client.transports.grpc import GrpcTransport
from a2a.client.transports.rest import RestTransport
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.client.errors import A2AClientJSONRPCError
from a2a.compat.v0_3 import a2a_v0_3_pb2 as pb2_v03
from a2a.compat.v0_3.conversions import (
    to_compat_cancel_task_request,
    to_compat_get_task_request,
    to_compat_send_message_request,
    to_compat_subscribe_to_task_request,
    to_compat_create_task_push_notification_config_request,
    to_compat_get_task_push_notification_config_request,
    to_core_message,
    to_core_stream_response,
    to_core_task,
    to_core_task_push_notification_config,
    to_core_agent_card
)
from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.proto_utils import ToProto, FromProto
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    CreateTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTasksRequest,
    ListTasksResponse,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig
)
from a2a.utils.errors import UnsupportedOperationError

logger = logging.getLogger(__name__)

class Compat03GrpcTransport(GrpcTransport):
    """gRPC transport for A2A v0.3.0 servers."""

    def __init__(self, channel: Any, card: AgentCard):
        super().__init__(channel, card)

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        compat_req = to_compat_send_message_request(request)
        legacy_pb_req = pb2_v03.SendMessageRequest(
            request=ToProto.message(compat_req.params.message),
            configuration=ToProto.message_send_configuration(compat_req.params.configuration),
            metadata=ToProto.metadata(compat_req.params.metadata)
        )
        request_bytes = legacy_pb_req.SerializeToString()

        for service_name in ['a2a.v1.A2AService', 'a2a.A2AService']:
            method = f'/{service_name}/SendStreamingMessage'
            call = self.channel.unary_stream(method, request_serializer=lambda x: x, response_deserializer=lambda x: x)
            try:
                async for res_bytes in call(request_bytes):
                    legacy_pb_resp = pb2_v03.StreamResponse()
                    legacy_pb_resp.ParseFromString(res_bytes)
                    pydantic_resp = FromProto.stream_response(legacy_pb_resp)
                    res_wrapper = types_v03.SendStreamingMessageSuccessResponse.model_construct(
                        jsonrpc="2.0",
                        result=pydantic_resp
                    )
                    yield to_core_stream_response(res_wrapper)
                return
            except grpc.aio.AioRpcError as e:
                logger.warning(f"DEBUG: gRPC call to {method} failed with {e.code()}: {e.details()}")
                if e.code() == grpc.StatusCode.UNIMPLEMENTED: continue
                raise
        raise RuntimeError("No compatible SendStreamingMessage service found")

    async def close(self) -> None:
        await self.channel.close()

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> SendMessageResponse:
        compat_req = to_compat_send_message_request(request)
        
        legacy_pb_req = pb2_v03.SendMessageRequest(
            request=ToProto.message(compat_req.params.message),
            configuration=ToProto.message_send_configuration(compat_req.params.configuration),
            metadata=ToProto.metadata(compat_req.params.metadata)
        )
        request_bytes = legacy_pb_req.SerializeToString()

        for service_name in ['a2a.v1.A2AService', 'a2a.A2AService', 'lf.a2a.v1.A2AService']:
            method = f'/{service_name}/SendMessage'
            call = self.channel.unary_unary(method, request_serializer=lambda x: x, response_deserializer=lambda x: x)
            try:
                res_bytes = await call(request_bytes)
                legacy_pb_resp = pb2_v03.SendMessageResponse()
                legacy_pb_resp.ParseFromString(res_bytes)
                pydantic_resp = FromProto.task_or_message(legacy_pb_resp)
                if hasattr(pydantic_resp, 'role'): # It's a Message
                    return SendMessageResponse(message=to_core_message(pydantic_resp))
                else:
                    return SendMessageResponse(task=to_core_task(pydantic_resp))
            except grpc.aio.AioRpcError as e:
                logger.warning(f"DEBUG: gRPC call to {method} failed with {e.code()}: {e.details()}")
                if e.code() == grpc.StatusCode.UNIMPLEMENTED:
                    details = e.details().lower()
                    if "method not found" in details or "not implemented" in details or "method not implemented" in details:
                         continue
                raise
        raise RuntimeError("No compatible SendMessage service found")

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        compat_req = to_compat_get_task_request(request)
        
        legacy_pb_req = pb2_v03.GetTaskRequest()
        legacy_pb_req.name = f"tasks/{compat_req.params.id}"
        if compat_req.params.history_length is not None:
             legacy_pb_req.history_length = compat_req.params.history_length

        request_bytes = legacy_pb_req.SerializeToString()
        
        for service_name in ['a2a.v1.A2AService', 'a2a.A2AService', 'lf.a2a.v1.A2AService']:
            method = f'/{service_name}/GetTask'
            call = self.channel.unary_unary(method, request_serializer=lambda x: x, response_deserializer=lambda x: x)
            try:
                res_bytes = await call(request_bytes)
                legacy_pb_resp = pb2_v03.Task()
                legacy_pb_resp.ParseFromString(res_bytes)
                pydantic_resp = FromProto.task(legacy_pb_resp)
                return to_core_task(pydantic_resp)
            except grpc.aio.AioRpcError as e:
                logger.warning(f"DEBUG: gRPC call to {method} failed with {e.code()}: {e.details()}")
                if e.code() == grpc.StatusCode.UNIMPLEMENTED:
                    details = e.details().lower()
                    if "method not found" in details or "not implemented" in details or "method not implemented" in details:
                         continue
                raise
        raise RuntimeError("No compatible GetTask service found")

    async def list_tasks(self, request, **kwargs):
        raise UnsupportedOperationError("list_tasks not supported in v0.3")

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        compat_req = to_compat_cancel_task_request(request)
        
        legacy_pb_req = pb2_v03.CancelTaskRequest()
        legacy_pb_req.name = f"tasks/{compat_req.params.id}"

        request_bytes = legacy_pb_req.SerializeToString()
        
        for service_name in ['a2a.v1.A2AService', 'a2a.A2AService', 'lf.a2a.v1.A2AService']:
            method = f'/{service_name}/CancelTask'
            call = self.channel.unary_unary(method, request_serializer=lambda x: x, response_deserializer=lambda x: x)
            try:
                res_bytes = await call(request_bytes)
                legacy_pb_resp = pb2_v03.Task()
                legacy_pb_resp.ParseFromString(res_bytes)
                pydantic_resp = FromProto.task(legacy_pb_resp)
                return to_core_task(pydantic_resp)
            except grpc.aio.AioRpcError as e:
                logger.warning(f"DEBUG: gRPC call to {method} failed with {e.code()}: {e.details()}")
                if e.code() == grpc.StatusCode.UNIMPLEMENTED:
                    details = e.details().lower()
                    if "method not found" in details or "not implemented" in details or "method not implemented" in details:
                         if "tasknotcancelableerror" not in details:
                              continue
                raise
        raise RuntimeError("No compatible CancelTask service found")

    async def set_task_callback(self, request, **kwargs):
        raise NotImplementedError()
    async def get_task_callback(self, request, **kwargs):
        raise NotImplementedError()
    
    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        compat_req = to_compat_subscribe_to_task_request(request)
        
        legacy_pb_req = pb2_v03.TaskSubscriptionRequest()
        legacy_pb_req.name = f"tasks/{compat_req.params.id}"

        request_bytes = legacy_pb_req.SerializeToString()
        
        for service_name in ['a2a.v1.A2AService', 'a2a.A2AService', 'lf.a2a.v1.A2AService']:
            method = f'/{service_name}/TaskSubscription'
            call = self.channel.unary_stream(method, request_serializer=lambda x: x, response_deserializer=lambda x: x)
            try:
                async for res_bytes in call(request_bytes):
                    legacy_pb_resp = pb2_v03.StreamResponse()
                    legacy_pb_resp.ParseFromString(res_bytes)
                    pydantic_resp = FromProto.stream_response(legacy_pb_resp)
                    res_wrapper = types_v03.SendStreamingMessageSuccessResponse.model_construct(
                        jsonrpc="2.0",
                        result=pydantic_resp
                    )
                    yield to_core_stream_response(res_wrapper)
                return
            except grpc.aio.AioRpcError as e:
                logger.warning(f"DEBUG: gRPC call to {method} failed with {e.code()}: {e.details()}")
                if e.code() == grpc.StatusCode.UNIMPLEMENTED:
                    details = e.details().lower()
                    if "method not found" in details or "not implemented" in details or "method not implemented" in details:
                         continue
                raise
        raise RuntimeError("No compatible TaskSubscription service found")


class Compat03RestTransport(RestTransport):
    """REST transport for A2A v0.3.0 servers."""

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        compat_req = to_compat_send_message_request(request)
        
        legacy_pb_req = pb2_v03.SendMessageRequest(
            request=ToProto.message(compat_req.params.message),
            configuration=ToProto.message_send_configuration(compat_req.params.configuration),
            metadata=ToProto.metadata(compat_req.params.metadata)
        )
        payload = json_format.MessageToDict(legacy_pb_req, preserving_proto_field_name=False)
        
        async with aconnect_sse(
            self.httpx_client,
            'POST',
            self.url.rstrip('/') + '/v1/message:stream',
            json=payload,
        ) as event_source:
            event_source.response.raise_for_status()
            async for sse in event_source.aiter_sse():
                if not sse.data:
                    continue
                data = json.loads(sse.data)
                
                legacy_pb_resp = pb2_v03.StreamResponse()
                if 'statusUpdate' in data or 'artifactUpdate' in data or 'task' in data or 'msg' in data or 'message' in data:
                    if 'message' in data and 'msg' not in data:
                        data['msg'] = data.pop('message')
                    json_format.ParseDict(data, legacy_pb_resp, ignore_unknown_fields=True)
                elif 'taskId' in data and 'status' in data:
                    legacy_pb_resp.task.CopyFrom(json_format.ParseDict(data, pb2_v03.Task(), ignore_unknown_fields=True))
                elif 'role' in data and 'messageId' in data:
                    legacy_pb_resp.msg.CopyFrom(json_format.ParseDict(data, pb2_v03.Message(), ignore_unknown_fields=True))

                pydantic_resp = FromProto.stream_response(legacy_pb_resp)
                res_wrapper = types_v03.SendStreamingMessageSuccessResponse.model_construct(
                    jsonrpc="2.0",
                    result=pydantic_resp
                )
                yield to_core_stream_response(res_wrapper)

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> SendMessageResponse:
        compat_req = to_compat_send_message_request(request)
        
        legacy_pb_req = pb2_v03.SendMessageRequest(
            request=ToProto.message(compat_req.params.message),
            configuration=ToProto.message_send_configuration(compat_req.params.configuration),
            metadata=ToProto.metadata(compat_req.params.metadata)
        )
        payload = json_format.MessageToDict(legacy_pb_req, preserving_proto_field_name=False)
        
        response = await self.httpx_client.post(
            self.url.rstrip('/') + '/v1/message:send',
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        
        legacy_pb_resp = pb2_v03.SendMessageResponse()
        if 'task' in data or 'message' in data or 'msg' in data:
             if 'message' in data and 'msg' not in data:
                  data['msg'] = data.pop('message')
             json_format.ParseDict(data, legacy_pb_resp, ignore_unknown_fields=True)
        elif 'id' in data and 'status' in data:
             legacy_pb_resp.task.CopyFrom(json_format.ParseDict(data, pb2_v03.Task(), ignore_unknown_fields=True))
        else:
             legacy_pb_resp.msg.CopyFrom(json_format.ParseDict(data, pb2_v03.Message(), ignore_unknown_fields=True))

        pydantic_resp = FromProto.task_or_message(legacy_pb_resp)
        if hasattr(pydantic_resp, 'role'):
            return SendMessageResponse(message=to_core_message(pydantic_resp))
        else:
            return SendMessageResponse(task=to_core_task(pydantic_resp))

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        compat_req = to_compat_get_task_request(request)
        task_id = compat_req.params.id
        
        response = await self.httpx_client.get(
            self.url.rstrip('/') + f'/v1/tasks/{task_id}',
        )
        response.raise_for_status()
        data = response.json()
        
        legacy_pb_resp = pb2_v03.Task()
        json_format.ParseDict(data, legacy_pb_resp, ignore_unknown_fields=True)
        pydantic_resp = FromProto.task(legacy_pb_resp)
        return to_core_task(pydantic_resp)

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> ListTasksResponse:
        raise UnsupportedOperationError("list_tasks not supported in v0.3")

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        compat_req = to_compat_cancel_task_request(request)
        task_id = compat_req.params.id
        
        response = await self.httpx_client.post(
            self.url.rstrip('/') + f'/v1/tasks/{task_id}:cancel',
        )
        response.raise_for_status()
        data = response.json()
        
        legacy_pb_resp = pb2_v03.Task()
        json_format.ParseDict(data, legacy_pb_resp, ignore_unknown_fields=True)
        pydantic_resp = FromProto.task(legacy_pb_resp)
        return to_core_task(pydantic_resp)

    async def set_task_callback(
        self,
        request: CreateTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
         compat_req = to_compat_create_task_push_notification_config_request(request)
         task_id = compat_req.params.task_id
         
         legacy_pb_req = pb2_v03.CreateTaskPushNotificationConfigRequest(
             parent=f"tasks/{task_id}",
             config_id=compat_req.params.push_notification_config.id,
             config=pb2_v03.TaskPushNotificationConfig(
                 push_notification_config=ToProto.push_notification_config(compat_req.params.push_notification_config)
             )
         )
         payload = json_format.MessageToDict(legacy_pb_req, preserving_proto_field_name=False)
         
         response = await self.httpx_client.post(
             self.url.rstrip('/') + f'/v1/tasks/{task_id}/pushNotificationConfigs',
             json=payload
         )
         response.raise_for_status()
         data = response.json()
         
         legacy_pb_resp = pb2_v03.TaskPushNotificationConfig()
         json_format.ParseDict(data, legacy_pb_resp, ignore_unknown_fields=True)
         pydantic_resp = FromProto.task_push_notification_config(legacy_pb_resp)
         return to_core_task_push_notification_config(pydantic_resp)

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
         compat_req = to_compat_get_task_push_notification_config_request(request)
         task_id = compat_req.params.task_id
         push_id = compat_req.params.id
         response = await self.httpx_client.get(
             self.url.rstrip('/') + f'/v1/tasks/{task_id}/pushNotificationConfigs/{push_id}',
         )
         response.raise_for_status()
         data = response.json()
         
         legacy_pb_resp = pb2_v03.TaskPushNotificationConfig()
         json_format.ParseDict(data, legacy_pb_resp, ignore_unknown_fields=True)
         pydantic_resp = FromProto.task_push_notification_config(legacy_pb_resp)
         return to_core_task_push_notification_config(pydantic_resp)

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        compat_req = to_compat_subscribe_to_task_request(request)
        task_id = compat_req.params.id
        
        async with aconnect_sse(
            self.httpx_client,
            'GET',
            self.url.rstrip('/') + f'/v1/tasks/{task_id}:subscribe',
        ) as event_source:
            event_source.response.raise_for_status()
            async for sse in event_source.aiter_sse():
                if not sse.data: continue
                data = json.loads(sse.data)
                
                legacy_pb_resp = pb2_v03.StreamResponse()
                if 'statusUpdate' in data or 'artifactUpdate' in data or 'task' in data or 'msg' in data or 'message' in data:
                    if 'message' in data and 'msg' not in data:
                         data['msg'] = data.pop('message')
                    json_format.ParseDict(data, legacy_pb_resp, ignore_unknown_fields=True)
                elif 'taskId' in data and 'status' in data:
                    legacy_pb_resp.task.CopyFrom(json_format.ParseDict(data, pb2_v03.Task(), ignore_unknown_fields=True))
                elif 'role' in data and 'messageId' in data:
                    legacy_pb_resp.msg.CopyFrom(json_format.ParseDict(data, pb2_v03.Message(), ignore_unknown_fields=True))
                
                pydantic_resp = FromProto.stream_response(legacy_pb_resp)
                res_wrapper = types_v03.SendStreamingMessageSuccessResponse.model_construct(
                    jsonrpc="2.0",
                    result=pydantic_resp
                )
                yield to_core_stream_response(res_wrapper)


class Compat03JsonRpcTransport(JsonRpcTransport):
    """JSON-RPC transport for A2A v0.3.0 servers."""

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> SendMessageResponse:
        compat_req = to_compat_send_message_request(request)
        rpc_params = compat_req.params.model_dump(mode='json', exclude_none=True)
        
        rpc_request = JSONRPC20Request(
            method='message/send',
            params=rpc_params,
            _id=str(uuid4()),
        )
        response_data = await self._send_request(cast('dict[str, Any]', rpc_request.data))
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise A2AClientJSONRPCError(json_rpc_response.error)
        
        legacy_pb_resp = pb2_v03.SendMessageResponse()
        data = json_rpc_response.result
        
        if 'messageId' in data or 'message_id' in data:
             json_format.ParseDict(data, legacy_pb_resp.msg, ignore_unknown_fields=True)
        elif 'id' in data and 'status' in data:
             json_format.ParseDict(data, legacy_pb_resp.task, ignore_unknown_fields=True)
        else:
             if 'message' in data and 'msg' not in data:
                  data['msg'] = data.pop('message')
             json_format.ParseDict(data, legacy_pb_resp, ignore_unknown_fields=True)

        pydantic_resp = FromProto.task_or_message(legacy_pb_resp)
        if hasattr(pydantic_resp, 'role'):
            return SendMessageResponse(message=to_core_message(pydantic_resp))
        else:
            return SendMessageResponse(task=to_core_task(pydantic_resp))

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        compat_req = to_compat_send_message_request(request)
        rpc_params = compat_req.params.model_dump(mode='json', exclude_none=True)
        
        rpc_request = JSONRPC20Request(
            method='message/stream',
            params=rpc_params,
            _id=str(uuid4()),
        )
        headers = {'X-A2A-Version': '0.3'}
        async with aconnect_sse(
            self.httpx_client,
            'POST',
            self.url,
            json=rpc_request.data,
            headers=headers,
            timeout=None,
        ) as event_source:
            async for sse in event_source.aiter_sse():
                if not sse.data.strip(): continue
                try:
                    data = json.loads(sse.data)
                    if isinstance(data, dict) and "result" in data:
                        res_v03_obj_dict = data["result"]
                    else:
                        res_v03_obj_dict = data
                        
                    if res_v03_obj_dict is None:
                        continue
                    
                    legacy_pb_resp = pb2_v03.StreamResponse()
                    wrapped_dict = {}
                    if 'messageId' in res_v03_obj_dict or 'message_id' in res_v03_obj_dict:
                        wrapped_dict['message'] = res_v03_obj_dict
                    elif 'artifactId' in res_v03_obj_dict or 'artifact_id' in res_v03_obj_dict:
                        wrapped_dict['artifactUpdate'] = res_v03_obj_dict
                    elif 'taskId' in res_v03_obj_dict or 'task_id' in res_v03_obj_dict:
                        wrapped_dict['statusUpdate'] = res_v03_obj_dict
                    elif 'id' in res_v03_obj_dict and 'status' in res_v03_obj_dict:
                        wrapped_dict['task'] = res_v03_obj_dict
                    else:
                        wrapped_dict = res_v03_obj_dict # maybe already wrapped?

                    json_format.ParseDict(wrapped_dict, legacy_pb_resp, ignore_unknown_fields=True)

                    pydantic_resp = FromProto.stream_response(legacy_pb_resp)
                    res_wrapper = types_v03.SendStreamingMessageSuccessResponse.model_construct(
                        jsonrpc="2.0",
                        result=pydantic_resp
                    )
                    yield to_core_stream_response(res_wrapper)
                except Exception as e:
                    logger.error(f"Failed to parse legacy stream event: {sse.data!r}, error: {e}")
                    continue

    async def subscribe_to_task(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        compat_req = to_compat_subscribe_to_task_request(request)
        rpc_request = JSONRPC20Request(
            method='tasks/resubscribe',
            params=compat_req.params.model_dump(mode='json', exclude_none=True),
            _id=str(uuid4()),
        )
        headers = {'X-A2A-Version': '0.3'}
        async with aconnect_sse(
            self.httpx_client,
            'POST',
            self.url,
            json=rpc_request.data,
            headers=headers,
            timeout=None,
        ) as event_source:
            async for sse in event_source.aiter_sse():
                if not sse.data.strip(): continue
                try:
                    data = json.loads(sse.data)
                    if isinstance(data, dict) and "result" in data:
                        res_v03_obj_dict = data["result"]
                    else:
                        res_v03_obj_dict = data
                    
                    if res_v03_obj_dict is None: continue
                    
                    legacy_pb_resp = pb2_v03.StreamResponse()
                    wrapped_dict = {}
                    if 'messageId' in res_v03_obj_dict or 'message_id' in res_v03_obj_dict:
                        wrapped_dict['message'] = res_v03_obj_dict
                    elif 'artifactId' in res_v03_obj_dict or 'artifact_id' in res_v03_obj_dict:
                        wrapped_dict['artifactUpdate'] = res_v03_obj_dict
                    elif 'taskId' in res_v03_obj_dict or 'task_id' in res_v03_obj_dict:
                        wrapped_dict['statusUpdate'] = res_v03_obj_dict
                    elif 'id' in res_v03_obj_dict and 'status' in res_v03_obj_dict:
                        wrapped_dict['task'] = res_v03_obj_dict
                    else:
                        wrapped_dict = res_v03_obj_dict

                    json_format.ParseDict(wrapped_dict, legacy_pb_resp, ignore_unknown_fields=True)

                    pydantic_resp = FromProto.stream_response(legacy_pb_resp)
                    res_wrapper = types_v03.SendStreamingMessageSuccessResponse.model_construct(
                        jsonrpc="2.0",
                        result=pydantic_resp
                    )
                    yield to_core_stream_response(res_wrapper)
                except Exception as e:
                    logger.error(f"Failed to parse legacy stream event: {sse.data!r}, error: {e}")
                    continue

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> ListTasksResponse:
        raise UnsupportedOperationError("list_tasks not supported in v0.3")

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        compat_req = to_compat_get_task_request(request)
        rpc_request = JSONRPC20Request(
            method='tasks/get',
            params=compat_req.params.model_dump(mode='json', exclude_none=True),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(cast('dict[str, Any]', rpc_request.data))
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise A2AClientJSONRPCError(json_rpc_response.error)
        
        data = json_rpc_response.result
        if 'task' in data: data = data['task']
        
        legacy_pb_resp = pb2_v03.Task()
        json_format.ParseDict(data, legacy_pb_resp, ignore_unknown_fields=True)
        pydantic_resp = FromProto.task(legacy_pb_resp)
        return to_core_task(pydantic_resp)

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        compat_req = to_compat_cancel_task_request(request)
        rpc_request = JSONRPC20Request(
            method='tasks/cancel',
            params=compat_req.params.model_dump(mode='json', exclude_none=True),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(cast('dict[str, Any]', rpc_request.data))
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise A2AClientJSONRPCError(json_rpc_response.error)
        
        data = json_rpc_response.result
        if 'task' in data: data = data['task']
        
        legacy_pb_resp = pb2_v03.Task()
        json_format.ParseDict(data, legacy_pb_resp, ignore_unknown_fields=True)
        pydantic_resp = FromProto.task(legacy_pb_resp)
        return to_core_task(pydantic_resp)

    async def get_extended_agent_card(
        self,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        rpc_request = JSONRPC20Request(
            method='agent/getExtendedAgentCard',
            params={},
            _id=str(uuid4()),
        )
        response_data = await self._send_request(cast('dict[str, Any]', rpc_request.data))
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise A2AClientJSONRPCError(json_rpc_response.error)
        
        legacy_pb_resp = pb2_v03.AgentCard()
        json_format.ParseDict(json_rpc_response.result, legacy_pb_resp, ignore_unknown_fields=True)
        pydantic_resp = FromProto.agent_card(legacy_pb_resp)
        return to_core_agent_card(pydantic_resp)

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        compat_req = to_compat_subscribe_to_task_request(request)
        rpc_request = JSONRPC20Request(
            method='tasks/resubscribe',
            params=compat_req.params.model_dump(mode='json', exclude_none=True),
            _id=str(uuid4()),
        )
        async with self.httpx_client.stream(
            'POST',
            self.url,
            json=rpc_request.data,
            timeout=None,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                line = line.strip()
                if not line: continue
                if line.startswith("data: "):
                    line = line[6:]
                if not line: continue
                try:
                    data = json.loads(line)
                    json_rpc_response = JSONRPC20Response(**data)
                    if json_rpc_response.error:
                        logger.error(f"JSON-RPC stream error: {json_rpc_response.error}")
                        raise A2AClientJSONRPCError(json_rpc_response.error)

                    res_v03_obj_dict = json_rpc_response.result
                    if res_v03_obj_dict is None:
                        logger.warning(f"JSON-RPC stream received empty result: {line}")
                        continue
                    
                    legacy_pb_resp = pb2_v03.StreamResponse()
                    wrapped_dict = {}
                    if 'messageId' in res_v03_obj_dict or 'message_id' in res_v03_obj_dict:
                        wrapped_dict['message'] = res_v03_obj_dict
                    elif 'artifactId' in res_v03_obj_dict or 'artifact_id' in res_v03_obj_dict:
                        wrapped_dict['artifactUpdate'] = res_v03_obj_dict
                    elif 'taskId' in res_v03_obj_dict or 'task_id' in res_v03_obj_dict:
                        wrapped_dict['statusUpdate'] = res_v03_obj_dict
                    elif 'id' in res_v03_obj_dict and 'status' in res_v03_obj_dict:
                        wrapped_dict['task'] = res_v03_obj_dict
                    else:
                        wrapped_dict = res_v03_obj_dict

                    json_format.ParseDict(wrapped_dict, legacy_pb_resp, ignore_unknown_fields=True)

                    pydantic_resp = FromProto.stream_response(legacy_pb_resp)
                    res_wrapper = types_v03.SendStreamingMessageSuccessResponse.model_construct(
                        jsonrpc="2.0",
                        result=pydantic_resp
                    )
                    yield to_core_stream_response(res_wrapper)
                except Exception as e:
                    logger.error(f"Failed to parse JSON-RPC stream line: {line!r}, error: {e}")
                    continue
