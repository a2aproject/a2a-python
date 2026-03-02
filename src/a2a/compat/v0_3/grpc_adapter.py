import logging
import grpc
from packaging.version import Version, InvalidVersion

from a2a.types import a2a_pb2 as pb2_v10
from a2a.compat.v0_3 import a2a_v0_3_pb2 as pb2_v03
from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3 import proto_utils
from a2a.compat.v0_3 import conversions
from a2a.server.context import ServerCallContext
from a2a.auth.user import UnauthenticatedUser

logger = logging.getLogger(__name__)

class Compat03GenericHandler(grpc.GenericRpcHandler):
    """
    A gRPC handler that intercepts legacy A2A v1 (v0.3 SDK) calls 
    and routes them to a modern v1.0 handler using a rigorous
    Protobuf -> Pydantic -> Protobuf conversion pipeline.
    """
    
    def __init__(self, handler):
        self.handler = handler

    def service(self, handler_call_details):
        # Look for the x-a2a-version header to determine if the request is legacy
        metadata = getattr(handler_call_details, 'invocation_metadata', ())
        is_legacy_request = True
        if metadata:
            for key, value in metadata:
                if key.lower() == 'x-a2a-version':
                    try:
                        if Version(value) >= Version('1.0.0'):
                            is_legacy_request = False
                    except InvalidVersion:
                        pass
                    break
        
        if not is_legacy_request:
            return None # Let the modern handler process this
                    
        method = handler_call_details.method
        if method.startswith('/a2a.v1.A2AService/') or method.startswith('/a2a.A2AService/'):
            sub_method = method.split('/')[-1]
            if sub_method == 'SendMessage':
                return grpc.unary_unary_rpc_method_handler(self._handle_send_message)
            elif sub_method == 'SendStreamingMessage':
                return grpc.unary_stream_rpc_method_handler(self._handle_send_streaming_message)
            elif sub_method == 'GetTask':
                return grpc.unary_unary_rpc_method_handler(self._handle_get_task)
            elif sub_method == 'CancelTask':
                return grpc.unary_unary_rpc_method_handler(self._handle_cancel_task)
            elif sub_method in ['TaskSubscription', 'SubscribeToTask']:
                return grpc.unary_stream_rpc_method_handler(self._handle_task_subscription)
        return None

    def _get_call_context(self):
        return ServerCallContext(
            user=UnauthenticatedUser(),
            state={},
            requested_extensions=[]
        )

    async def _handle_send_message(self, request_bytes, context):
        try:
            legacy_req = pb2_v03.SendMessageRequest()
            legacy_req.ParseFromString(request_bytes)

            pydantic_params = proto_utils.FromProto.message_send_params(legacy_req)

            pydantic_req = types_v03.SendMessageRequest(
                method="message/send",
                params=pydantic_params,
                id="1"
            )

            core_req = conversions.to_core_send_message_request(pydantic_req)

            call_context = self._get_call_context()
            core_resp_payload = await self.handler.on_message_send(core_req, call_context)

            if isinstance(core_resp_payload, pb2_v10.Task):
                pydantic_task = conversions.to_compat_task(core_resp_payload)
                legacy_resp = proto_utils.ToProto.task_or_message(pydantic_task)
            else:
                pydantic_msg = conversions.to_compat_message(core_resp_payload)
                legacy_resp = proto_utils.ToProto.task_or_message(pydantic_msg)
                
            return legacy_resp.SerializeToString()
            
        except Exception as e:
            logger.exception("Error in compat gRPC _handle_send_message v2")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def _handle_send_streaming_message(self, request_bytes, context):
        try:
            legacy_req = pb2_v03.SendMessageRequest()
            legacy_req.ParseFromString(request_bytes)

            pydantic_params = proto_utils.FromProto.message_send_params(legacy_req)
            pydantic_req = types_v03.SendMessageRequest(
                method="message/send",
                params=pydantic_params,
                id="1"
            )
            core_req = conversions.to_core_send_message_request(pydantic_req)

            call_context = self._get_call_context()
            stream = self.handler.on_message_send_stream(core_req, call_context)
            
            async for core_event in stream:
                if isinstance(core_event, pb2_v10.Message):
                    compat_event = conversions.to_compat_message(core_event)
                elif isinstance(core_event, pb2_v10.Task):
                    compat_event = conversions.to_compat_task(core_event)
                elif isinstance(core_event, pb2_v10.TaskStatusUpdateEvent):
                    compat_event = conversions.to_compat_task_status_update_event(core_event)
                elif isinstance(core_event, pb2_v10.TaskArtifactUpdateEvent):
                    compat_event = conversions.to_compat_task_artifact_update_event(core_event)
                else:
                    raise ValueError(f"Unknown event type: {type(core_event)}")
                    
                legacy_stream_resp = proto_utils.ToProto.stream_response(compat_event)
                yield legacy_stream_resp.SerializeToString()
                
        except Exception as e:
            logger.exception("Error in compat gRPC _handle_send_streaming_message v2")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def _handle_get_task(self, request_bytes, context):
        try:
            legacy_req = pb2_v03.GetTaskRequest()
            legacy_req.ParseFromString(request_bytes)
            
            pydantic_params = proto_utils.FromProto.task_query_params(legacy_req)
            pydantic_req = types_v03.GetTaskRequest(
                method="tasks/get",
                params=pydantic_params,
                id="1"
            )
            core_req = conversions.to_core_get_task_request(pydantic_req)
            
            call_context = self._get_call_context()
            core_task = await self.handler.on_get_task(core_req, call_context)
            
            pydantic_task = conversions.to_compat_task(core_task)
            legacy_pb_task = proto_utils.ToProto.task(pydantic_task)
            return legacy_pb_task.SerializeToString()
        except Exception as e:
            logger.exception("Error in compat gRPC _handle_get_task v2")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def _handle_cancel_task(self, request_bytes, context):
        try:
            legacy_req = pb2_v03.CancelTaskRequest()
            legacy_req.ParseFromString(request_bytes)
            
            pydantic_params = proto_utils.FromProto.task_id_params(legacy_req)
            pydantic_req = types_v03.CancelTaskRequest(
                method="tasks/cancel",
                params=pydantic_params,
                id="1"
            )
            core_req = conversions.to_core_cancel_task_request(pydantic_req)
            
            call_context = self._get_call_context()
            core_task = await self.handler.on_cancel_task(core_req, call_context)
            
            pydantic_task = conversions.to_compat_task(core_task)
            legacy_pb_task = proto_utils.ToProto.task(pydantic_task)
            return legacy_pb_task.SerializeToString()
        except Exception as e:
            logger.exception("Error in compat gRPC _handle_cancel_task v2")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def _handle_task_subscription(self, request_bytes, context):
        try:
            legacy_req = pb2_v03.TaskSubscriptionRequest()
            legacy_req.ParseFromString(request_bytes)
            
            pydantic_params = proto_utils.FromProto.task_id_params(legacy_req)
            pydantic_req = types_v03.TaskResubscriptionRequest(
                method="tasks/resubscribe",
                params=pydantic_params,
                id="1"
            )
            core_req = conversions.to_core_subscribe_to_task_request(pydantic_req)
            
            call_context = self._get_call_context()
            stream = self.handler.on_subscribe_to_task(core_req, call_context)
            
            async for core_event in stream:
                if isinstance(core_event, pb2_v10.Message):
                    compat_event = conversions.to_compat_message(core_event)
                elif isinstance(core_event, pb2_v10.Task):
                    compat_event = conversions.to_compat_task(core_event)
                elif isinstance(core_event, pb2_v10.TaskStatusUpdateEvent):
                    compat_event = conversions.to_compat_task_status_update_event(core_event)
                elif isinstance(core_event, pb2_v10.TaskArtifactUpdateEvent):
                    compat_event = conversions.to_compat_task_artifact_update_event(core_event)
                else:
                    raise ValueError(f"Unknown event type: {type(core_event)}")
                    
                legacy_stream_resp = proto_utils.ToProto.stream_response(compat_event)
                yield legacy_stream_resp.SerializeToString()
        except Exception as e:
            logger.exception("Error in compat gRPC _handle_task_subscription v2")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

def mount_compat_grpc_servicer(server, handler):
    """
    Mounts a gRPC generic handler that responds to legacy a2a.A2AService calls.
    """
    server.add_generic_rpc_handlers((Compat03GenericHandler(handler),))
