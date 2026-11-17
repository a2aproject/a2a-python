import pytest

from a2a.client.transports.rest import RestTransport
from a2a.compat.v0_3.rest_transport import CompatRestTransport
from a2a.helpers.proto_helpers import new_text_message
from a2a.types.a2a_pb2 import (
    AgentCard,
    DeleteTaskPushNotificationConfigRequest,
    GetTaskRequest,
    SendMessageRequest,
    SubscribeToTaskRequest,
)
from a2a.utils._httpx_compat import Request, Response


pytestmark = pytest.mark.httpx2


@pytest.mark.asyncio
@pytest.mark.parametrize('http_library', ['httpx', 'httpx2'])
@pytest.mark.parametrize('legacy', [False, True], ids=['v1', 'v0_3'])
@pytest.mark.parametrize(
    'endpoint',
    [
        'http://agent/api',
        'http://agent/api/',
        'http://agent/api/?api-version=v1&tag=a&tag=b&blank=&historyLength=99&resource=a/',
    ],
)
async def test_rest_endpoint_queries(
    http_library: str, legacy: bool, endpoint: str
) -> None:
    http = pytest.importorskip(http_library)
    prefix = '/api/v1' if legacy else '/api'
    expected_paths = [
        f'{prefix}/message:send',
        f'{prefix}/tasks/task1',
        f'{prefix}/message:stream',
        f'{prefix}/tasks/task1:subscribe',
        f'{prefix}/tasks/task1/pushNotificationConfigs/config1',
    ]
    seen: list[str] = []

    def handle(request: Request) -> Response:
        seen.append(request.url.path)
        assert request.url.path == expected_paths[len(seen) - 1]
        if '?' in endpoint:
            assert request.url.params['api-version'] == 'v1'
            assert request.url.params.get_list('tag') == ['a', 'b']
            assert request.url.params['blank'] == ''
            assert request.url.params['resource'] == 'a/'
        if request.url.path.endswith('/tasks/task1'):
            assert request.url.params['historyLength'] == '2'
            return http.Response(200, json={'id': 'task1'})
        if request.method == 'DELETE':
            return http.Response(200, json={})
        if len(seen) > 2:
            return http.Response(
                200,
                headers={'content-type': 'text/event-stream'},
                content='data: {"task":{"id":"task1"}}\n\n',
            )
        return http.Response(200, json={'task': {'id': 'task1'}})

    async with http.AsyncClient(transport=http.MockTransport(handle)) as client:
        transport = (CompatRestTransport if legacy else RestTransport)(
            client, AgentCard(), endpoint, is_shared_httpx=True
        )
        message = SendMessageRequest(message=new_text_message(text='hello'))
        assert (await transport.send_message(message)).task.id == 'task1'
        assert (
            await transport.get_task(
                GetTaskRequest(id='task1', history_length=2)
            )
        ).id == 'task1'
        events = [
            event async for event in transport.send_message_streaming(message)
        ]
        assert [event.task.id for event in events] == ['task1']
        events = [
            event
            async for event in transport.subscribe(
                SubscribeToTaskRequest(id='task1')
            )
        ]
        assert [event.task.id for event in events] == ['task1']
        await transport.delete_task_push_notification_config(
            DeleteTaskPushNotificationConfigRequest(
                task_id='task1', id='config1'
            )
        )
    assert seen == expected_paths
