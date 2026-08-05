import unittest

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from a2a.server.tasks.base_push_notification_sender import (
    BasePushNotificationSender,
)
from a2a.types.a2a_pb2 import (
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict


def _create_sample_task(
    task_id: str = 'task123',
    status_state: TaskState = TaskState.TASK_STATE_COMPLETED,
) -> Task:
    return Task(
        id=task_id,
        context_id='ctx456',
        status=TaskStatus(state=status_state),
    )


def _create_sample_push_config(
    url: str = 'http://example.com/callback',
    config_id: str = 'cfg1',
    token: str | None = None,
) -> TaskPushNotificationConfig:
    return TaskPushNotificationConfig(id=config_id, url=url, token=token)


class TestBasePushNotificationSender(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.mock_httpx_client = AsyncMock(spec=httpx.AsyncClient)
        self.mock_config_store = AsyncMock()
        # Keep DNS hermetic: pretend every test URL resolves to a public IP.
        getaddrinfo_patch = patch(
            'a2a.server.tasks.base_push_notification_sender.socket.getaddrinfo',
            return_value=[(2, 1, 6, '', ('93.184.216.34', 80))],
        )
        self.addCleanup(getaddrinfo_patch.stop)
        getaddrinfo_patch.start()
        self.sender = BasePushNotificationSender(
            httpx_client=self.mock_httpx_client,
            config_store=self.mock_config_store,
        )

    def test_constructor_stores_client_and_config_store(self) -> None:
        self.assertEqual(self.sender._client, self.mock_httpx_client)
        self.assertEqual(self.sender._config_store, self.mock_config_store)

    async def test_send_notification_success(self) -> None:
        task_id = 'task_send_success'
        task_data = _create_sample_task(task_id=task_id)
        config = _create_sample_push_config(url='http://notify.me/here')
        self.mock_config_store.get_info_for_dispatch.return_value = [config]

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.sender.send_notification(task_id, task_data)

        self.mock_config_store.get_info_for_dispatch.assert_awaited_once_with(
            task_data.id
        )

        # assert httpx_client post method got invoked with right parameters
        self.mock_httpx_client.post.assert_awaited_once_with(
            config.url,
            json=MessageToDict(StreamResponse(task=task_data)),
            headers=None,
        )
        mock_response.raise_for_status.assert_called_once()

    async def test_send_notification_with_token_success(self) -> None:
        task_id = 'task_send_success'
        task_data = _create_sample_task(task_id=task_id)
        config = _create_sample_push_config(
            url='http://notify.me/here', token='unique_token'
        )
        self.mock_config_store.get_info_for_dispatch.return_value = [config]

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.sender.send_notification(task_id, task_data)

        self.mock_config_store.get_info_for_dispatch.assert_awaited_once_with(
            task_data.id
        )

        # assert httpx_client post method got invoked with right parameters
        self.mock_httpx_client.post.assert_awaited_once_with(
            config.url,
            json=MessageToDict(StreamResponse(task=task_data)),
            headers={'X-A2A-Notification-Token': 'unique_token'},
        )
        mock_response.raise_for_status.assert_called_once()

    async def test_send_notification_no_config(self) -> None:
        task_id = 'task_send_no_config'
        task_data = _create_sample_task(task_id=task_id)
        self.mock_config_store.get_info_for_dispatch.return_value = []

        await self.sender.send_notification(task_id, task_data)

        self.mock_config_store.get_info_for_dispatch.assert_awaited_once_with(
            task_id
        )
        self.mock_httpx_client.post.assert_not_called()

    @patch('a2a.server.tasks.base_push_notification_sender.logger')
    async def test_send_notification_http_status_error(
        self, mock_logger: MagicMock
    ) -> None:
        task_id = 'task_send_http_err'
        task_data = _create_sample_task(task_id=task_id)
        config = _create_sample_push_config(url='http://notify.me/http_error')
        self.mock_config_store.get_info_for_dispatch.return_value = [config]

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 404
        mock_response.text = 'Not Found'
        http_error = httpx.HTTPStatusError(
            'Not Found', request=MagicMock(), response=mock_response
        )
        self.mock_httpx_client.post.side_effect = http_error

        await self.sender.send_notification(task_id, task_data)

        self.mock_config_store.get_info_for_dispatch.assert_awaited_once_with(
            task_id
        )
        self.mock_httpx_client.post.assert_awaited_once_with(
            config.url,
            json=MessageToDict(StreamResponse(task=task_data)),
            headers=None,
        )
        mock_logger.exception.assert_called_once()

    async def test_send_notification_multiple_configs(self) -> None:
        task_id = 'task_multiple_configs'
        task_data = _create_sample_task(task_id=task_id)
        config1 = _create_sample_push_config(
            url='http://notify.me/cfg1', config_id='cfg1'
        )
        config2 = _create_sample_push_config(
            url='http://notify.me/cfg2', config_id='cfg2'
        )
        self.mock_config_store.get_info_for_dispatch.return_value = [
            config1,
            config2,
        ]

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.sender.send_notification(task_id, task_data)

        self.mock_config_store.get_info_for_dispatch.assert_awaited_once_with(
            task_id
        )
        self.assertEqual(self.mock_httpx_client.post.call_count, 2)

        # Check calls for config1
        self.mock_httpx_client.post.assert_any_call(
            config1.url,
            json=MessageToDict(StreamResponse(task=task_data)),
            headers=None,
        )
        # Check calls for config2
        self.mock_httpx_client.post.assert_any_call(
            config2.url,
            json=MessageToDict(StreamResponse(task=task_data)),
            headers=None,
        )
        mock_response.raise_for_status.call_count = 2

    async def test_send_notification_status_update_event(self) -> None:
        task_id = 'task_status_update'
        event = TaskStatusUpdateEvent(
            task_id=task_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        config = _create_sample_push_config(url='http://notify.me/status')
        self.mock_config_store.get_info_for_dispatch.return_value = [config]

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.sender.send_notification(task_id, event)

        self.mock_config_store.get_info_for_dispatch.assert_awaited_once_with(
            task_id
        )
        self.mock_httpx_client.post.assert_awaited_once_with(
            config.url,
            json=MessageToDict(StreamResponse(status_update=event)),
            headers=None,
        )

    async def test_send_notification_artifact_update_event(self) -> None:
        task_id = 'task_artifact_update'
        event = TaskArtifactUpdateEvent(
            task_id=task_id,
            append=True,
        )
        config = _create_sample_push_config(url='http://notify.me/artifact')
        self.mock_config_store.get_info_for_dispatch.return_value = [config]

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.sender.send_notification(task_id, event)

        self.mock_config_store.get_info_for_dispatch.assert_awaited_once_with(
            task_id
        )
        self.mock_httpx_client.post.assert_awaited_once_with(
            config.url,
            json=MessageToDict(StreamResponse(artifact_update=event)),
            headers=None,
        )


_GAI = 'a2a.server.tasks.base_push_notification_sender.socket.getaddrinfo'


def _gai_result(ip: str, port: int = 80):
    return [(2, 1, 6, '', (ip, port))]


class TestPushUrlValidation(unittest.IsolatedAsyncioTestCase):
    """SSRF hardening: client-supplied push URLs must not reach non-public
    destinations unless the operator explicitly opts out."""

    def setUp(self) -> None:
        self.mock_httpx_client = AsyncMock(spec=httpx.AsyncClient)
        self.mock_config_store = AsyncMock()
        self.sender = BasePushNotificationSender(
            httpx_client=self.mock_httpx_client,
            config_store=self.mock_config_store,
        )

    async def _dispatch(self, url: str) -> None:
        task = _create_sample_task()
        config = _create_sample_push_config(url=url)
        self.mock_config_store.get_info_for_dispatch.return_value = [config]
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response
        await self.sender.send_notification(task.id, task)

    async def test_metadata_endpoint_blocked(self) -> None:
        with patch(_GAI, return_value=_gai_result('169.254.169.254')):
            await self._dispatch('http://metadata.google.internal/latest')
        self.mock_httpx_client.post.assert_not_called()

    async def test_loopback_blocked(self) -> None:
        with patch(_GAI, return_value=_gai_result('127.0.0.1')):
            await self._dispatch('http://localhost:8080/admin')
        self.mock_httpx_client.post.assert_not_called()

    async def test_private_range_blocked(self) -> None:
        with patch(_GAI, return_value=_gai_result('10.0.0.5')):
            await self._dispatch('http://internal-service/endpoint')
        self.mock_httpx_client.post.assert_not_called()

    async def test_non_http_scheme_blocked(self) -> None:
        await self._dispatch('ftp://example.com/file')
        self.mock_httpx_client.post.assert_not_called()

    async def test_unresolvable_host_blocked_fail_closed(self) -> None:
        import socket as _socket

        with patch(_GAI, side_effect=_socket.gaierror('no DNS')):
            await self._dispatch('http://does-not-resolve.invalid/')
        self.mock_httpx_client.post.assert_not_called()

    async def test_public_host_allowed(self) -> None:
        with patch(_GAI, return_value=_gai_result('93.184.216.34')):
            await self._dispatch('http://notify.me/here')
        self.mock_httpx_client.post.assert_awaited_once()

    async def test_allow_private_opt_out(self) -> None:
        sender = BasePushNotificationSender(
            httpx_client=self.mock_httpx_client,
            config_store=self.mock_config_store,
            allow_private_push_urls=True,
        )
        task = _create_sample_task()
        config = _create_sample_push_config(url='http://localhost:9000/hook')
        self.mock_config_store.get_info_for_dispatch.return_value = [config]
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response
        await sender.send_notification(task.id, task)
        self.mock_httpx_client.post.assert_awaited_once()
