from collections.abc import AsyncIterator
from typing import Any

import grpc
import pytest

from a2a.client.transports.grpc import GrpcTransport
from a2a.types import a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    GetTaskRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
)
from a2a.utils.errors import (
    A2A_ERROR_REASONS,
    InvalidParamsError,
    TaskNotFoundError,
)
from google.protobuf import any_pb2
from google.rpc import error_details_pb2, status_pb2


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize('streaming', [False, True], ids=['unary', 'stream'])
@pytest.mark.parametrize('reverse_details', [False, True])
@pytest.mark.parametrize('case', ['metadata', 'validation', 'foreign', 'empty'])
async def test_grpc_error_metadata(
    streaming: bool, reverse_details: bool, case: str
) -> None:
    error_cls = (
        InvalidParamsError if case == 'validation' else TaskNotFoundError
    )
    code = (
        grpc.StatusCode.INVALID_ARGUMENT
        if case == 'validation'
        else grpc.StatusCode.NOT_FOUND
    )
    metadata = {} if case == 'empty' else {'taskId': 'job-1'}
    expected: dict[str, Any] | None = dict(metadata) or None
    error_info = error_details_pb2.ErrorInfo(
        reason=A2A_ERROR_REASONS[error_cls],
        domain='a2a-protocol.org',
        metadata=metadata,
    )
    details = [any_pb2.Any()]
    details[0].Pack(error_info)
    if case == 'validation':
        error_info.metadata['errors'] = 'must-not-replace-field-violations'
        details[0].Pack(error_info)
        bad_request = error_details_pb2.BadRequest()
        bad_request.field_violations.add(field='id', description='invalid id')
        detail = any_pb2.Any()
        detail.Pack(bad_request)
        details.append(detail)
        expected = {
            **metadata,
            'errors': [{'field': 'id', 'message': 'invalid id'}],
        }
    elif case == 'foreign':
        detail = any_pb2.Any()
        detail.Pack(
            error_details_pb2.ErrorInfo(
                reason=A2A_ERROR_REASONS[error_cls],
                domain='provider.example',
                metadata={'provider': 'not-A2A-data'},
            )
        )
        details.append(detail)
    if reverse_details:
        details.reverse()
    message = 'Cannot access task'
    status = status_pb2.Status(
        code=code.value[0], message=message, details=details
    )
    trailing = (('grpc-status-details-bin', status.SerializeToString()),)

    class RejectingAgent(a2a_pb2_grpc.A2AServiceServicer):
        async def GetTask(
            self, request: GetTaskRequest, context: grpc.aio.ServicerContext
        ) -> Task:
            context.set_trailing_metadata(trailing)
            await context.abort(code, message)
            return Task()

        async def SubscribeToTask(
            self,
            request: SubscribeToTaskRequest,
            context: grpc.aio.ServicerContext,
        ) -> AsyncIterator[StreamResponse]:
            context.set_trailing_metadata(trailing)
            await context.abort(code, message)
            yield StreamResponse()

    server = grpc.aio.server()
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(RejectingAgent(), server)
    port = server.add_insecure_port('127.0.0.1:0')
    assert port > 0
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f'127.0.0.1:{port}') as channel:
            transport = GrpcTransport(channel, None)
            with pytest.raises(error_cls) as exc:
                if streaming:
                    _ = [
                        event
                        async for event in transport.subscribe(
                            SubscribeToTaskRequest(id='job-1')
                        )
                    ]
                else:
                    await transport.get_task(GetTaskRequest(id='job-1'))
            assert str(exc.value) == message
            assert exc.value.data == expected
    finally:
        await server.stop(None)
