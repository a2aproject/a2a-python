"""A v0.3 agent cannot be asked for an empty history, so the compat transports
must apply the v1.0 `history_length` semantics to the task they return.

In v0.3 `history_length = 0` means *unlimited* (see
`tests/compat/v0_3/test_proto_utils.py::test_message_send_configuration_history_length`),
while in v1.0 it means *no history*. Forwarding `0` to a v0.3 agent therefore
returns the whole history, so each compat transport has to truncate locally.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.compat.v0_3.grpc_transport import CompatGrpcTransport
from a2a.compat.v0_3.jsonrpc_transport import CompatJsonRpcTransport
from a2a.compat.v0_3.rest_transport import CompatRestTransport
from a2a.types.a2a_pb2 import (
    AgentCard,
    GetTaskRequest,
    Role,
    SendMessageRequest,
    TaskState,
)


URL = 'http://example.com'


def _task_dict(proto_style: bool) -> dict:
    """A v0.3 task payload carrying three history messages.

    `proto_style` selects the enum spelling used by the protobuf-backed
    transports; the JSON-RPC transport validates against the v0.3 pydantic
    models, which use the short names.
    """
    role = 'ROLE_USER' if proto_style else 'user'
    state = 'TASK_STATE_WORKING' if proto_style else 'working'
    return {
        'id': 'task-1',
        'contextId': 'ctx-1',
        'status': {'state': state},
        'history': [
            {'messageId': f'm{i}', 'role': role, 'parts': []} for i in range(3)
        ],
    }


def _rpc_ok(result: dict) -> dict:
    return {'id': '0', 'jsonrpc': '2.0', 'result': result}


def _rest_transport() -> CompatRestTransport:
    t = CompatRestTransport(
        httpx_client=AsyncMock(),
        agent_card=AgentCard(name='a', version='1'),
        url=URL,
    )

    async def _execute_request(method, path, context=None, **kwargs):
        return _task_dict(proto_style=True)

    t._execute_request = _execute_request
    return t


def _jsonrpc_transport() -> CompatJsonRpcTransport:
    t = CompatJsonRpcTransport(
        httpx_client=AsyncMock(),
        agent_card=AgentCard(name='a', version='1'),
        url=URL,
    )

    async def _send_request(payload_, context=None):
        return _rpc_ok(_task_dict(proto_style=False))

    t._send_request = _send_request
    return t


def _grpc_transport() -> CompatGrpcTransport:
    import a2a.compat.v0_3.a2a_v0_3_pb2 as v03

    payload = _task_dict(proto_style=True)
    task = v03.Task()
    task.id = payload['id']
    task.context_id = payload['contextId']
    setattr(task.status, 'state', TaskState.TASK_STATE_WORKING)
    for m in payload['history']:
        msg = task.history.add()
        msg.message_id = m['messageId']
        msg.role = Role.ROLE_USER

    t = CompatGrpcTransport(
        channel=MagicMock(), agent_card=AgentCard(name='a', version='1')
    )

    async def _get_task(req, metadata=None):
        return task

    t.stub = MagicMock(GetTask=AsyncMock(side_effect=_get_task))
    return t


TRANSPORTS = [_rest_transport, _jsonrpc_transport, _grpc_transport]


class TestCompatGetTaskHistoryLength:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('factory', TRANSPORTS)
    async def test_history_length_zero_drops_history(self, factory):
        t = factory()
        task = await t.get_task(GetTaskRequest(id='task-1', history_length=0))
        assert len(task.history) == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize('factory', TRANSPORTS)
    async def test_history_length_keeps_last_n(self, factory):
        t = factory()
        task = await t.get_task(GetTaskRequest(id='task-1', history_length=1))
        assert [m.message_id for m in task.history] == ['m2']

    @pytest.mark.asyncio
    @pytest.mark.parametrize('factory', TRANSPORTS)
    async def test_unset_history_length_keeps_history(self, factory):
        t = factory()
        task = await t.get_task(GetTaskRequest(id='task-1'))
        assert len(task.history) == 3


class TestCompatSendMessageHistoryLength:
    @pytest.mark.asyncio
    async def test_rest_send_message_applies_history_length(self):
        t = CompatRestTransport(
            httpx_client=AsyncMock(),
            agent_card=AgentCard(name='a', version='1'),
            url=URL,
        )

        async def _execute_request(method, path, context=None, **kwargs):
            return {'task': _task_dict(proto_style=True)}

        t._execute_request = _execute_request
        req = SendMessageRequest()
        req.configuration.history_length = 0
        resp = await t.send_message(req)
        assert resp.HasField('task')
        assert len(resp.task.history) == 0

    @pytest.mark.asyncio
    async def test_jsonrpc_send_message_applies_history_length(self):
        t = CompatJsonRpcTransport(
            httpx_client=AsyncMock(),
            agent_card=AgentCard(name='a', version='1'),
            url=URL,
        )

        async def _send_request(payload_, context=None):
            payload = _task_dict(proto_style=False)
            payload['kind'] = 'task'
            return _rpc_ok(payload)

        t._send_request = _send_request
        req = SendMessageRequest()
        req.configuration.history_length = 0
        resp = await t.send_message(req)
        assert resp.HasField('task')
        assert len(resp.task.history) == 0
