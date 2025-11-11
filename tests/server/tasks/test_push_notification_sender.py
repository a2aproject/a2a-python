import unittest

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from a2a.server.tasks.base_push_notification_sender import (
    BasePushNotificationSender,
)
from a2a.types import (
    PushNotificationConfig,
    Task,
    TaskState,
    TaskStatus,
)


def create_sample_task(
    task_id: str = 'task123', status_state: TaskState = TaskState.completed
) -> Task:
    return Task(
        id=task_id,
        context_id='ctx456',
        status=TaskStatus(state=status_state),
    )


def create_sample_push_config(
    url: str = 'http://example.com/callback',
    config_id: str = 'cfg1',
    token: str | None = None,
) -> PushNotificationConfig:
    return PushNotificationConfig(id=config_id, url=url, token=token)


class TestBasePushNotificationSender(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.mock_httpx_client = AsyncMock(spec=httpx.AsyncClient)
        self.mock_config_store = AsyncMock()
        self.sender = BasePushNotificationSender(
            httpx_client=self.mock_httpx_client,
            config_store=self.mock_config_store,
        )

    def test_constructor_stores_client_and_config_store(self) -> None:
        self.assertEqual(self.sender._client, self.mock_httpx_client)
        self.assertEqual(self.sender._config_store, self.mock_config_store)

    async def test_send_notification_success(self) -> None:
        task_id = 'task_send_success'
        task_data = create_sample_task(task_id=task_id)
        config = create_sample_push_config(url='http://notify.me/here')
        self.mock_config_store.get_info.return_value = [config]

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.sender.send_notification(task_data)

        self.mock_config_store.get_info.assert_awaited_once_with

        # assert httpx_client post method got invoked with right parameters
        self.mock_httpx_client.post.assert_awaited_once_with(
            config.url,
            json=task_data.model_dump(mode='json', exclude_none=True),
            headers=None,
        )
        mock_response.raise_for_status.assert_called_once()

    async def test_send_notification_with_token_success(self) -> None:
        task_id = 'task_send_success'
        task_data = create_sample_task(task_id=task_id)
        config = create_sample_push_config(
            url='http://notify.me/here', token='unique_token'
        )
        self.mock_config_store.get_info.return_value = [config]

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.sender.send_notification(task_data)

        self.mock_config_store.get_info.assert_awaited_once_with

        # assert httpx_client post method got invoked with right parameters
        self.mock_httpx_client.post.assert_awaited_once_with(
            config.url,
            json=task_data.model_dump(mode='json', exclude_none=True),
            headers={'X-A2A-Notification-Token': 'unique_token'},
        )
        mock_response.raise_for_status.assert_called_once()

    async def test_send_notification_no_config(self) -> None:
        task_id = 'task_send_no_config'
        task_data = create_sample_task(task_id=task_id)
        self.mock_config_store.get_info.return_value = []

        await self.sender.send_notification(task_data)

        self.mock_config_store.get_info.assert_awaited_once_with(task_id)
        self.mock_httpx_client.post.assert_not_called()

    @patch('a2a.server.tasks.base_push_notification_sender.logger')
    async def test_send_notification_http_status_error(
        self, mock_logger: MagicMock
    ) -> None:
        task_id = 'task_send_http_err'
        task_data = create_sample_task(task_id=task_id)
        config = create_sample_push_config(url='http://notify.me/http_error')
        self.mock_config_store.get_info.return_value = [config]

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 404
        mock_response.text = 'Not Found'
        http_error = httpx.HTTPStatusError(
            'Not Found', request=MagicMock(), response=mock_response
        )
        self.mock_httpx_client.post.side_effect = http_error

        await self.sender.send_notification(task_data)

        self.mock_config_store.get_info.assert_awaited_once_with(task_id)
        self.mock_httpx_client.post.assert_awaited_once_with(
            config.url,
            json=task_data.model_dump(mode='json', exclude_none=True),
            headers=None,
        )
        mock_logger.exception.assert_called_once()

    async def test_send_notification_multiple_configs(self) -> None:
        task_id = 'task_multiple_configs'
        task_data = create_sample_task(task_id=task_id)
        config1 = create_sample_push_config(
            url='http://notify.me/cfg1', config_id='cfg1'
        )
        config2 = create_sample_push_config(
            url='http://notify.me/cfg2', config_id='cfg2'
        )
        self.mock_config_store.get_info.return_value = [config1, config2]

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.sender.send_notification(task_data)

        self.mock_config_store.get_info.assert_awaited_once_with(task_id)
        self.assertEqual(self.mock_httpx_client.post.call_count, 2)

        # Check calls for config1
        self.mock_httpx_client.post.assert_any_call(
            config1.url,
            json=task_data.model_dump(mode='json', exclude_none=True),
            headers=None,
        )
        # Check calls for config2
        self.mock_httpx_client.post.assert_any_call(
            config2.url,
            json=task_data.model_dump(mode='json', exclude_none=True),
            headers=None,
        )
        mock_response.raise_for_status.call_count = 2
