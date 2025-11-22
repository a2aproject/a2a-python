import unittest

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from a2a.server.tasks.base_push_notification_sender import (
    BasePushNotificationSender,
)
from a2a.server.tasks.inmemory_push_notification_config_store import (
    InMemoryPushNotificationConfigStore,
)
from a2a.types import PushNotificationConfig, Task, TaskState, TaskStatus


# Suppress logging for cleaner test output, can be enabled for debugging
# logging.disable(logging.CRITICAL)


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


class TestInMemoryPushNotifier(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.mock_httpx_client = AsyncMock(spec=httpx.AsyncClient)
        self.config_store = InMemoryPushNotificationConfigStore()
        self.notifier = BasePushNotificationSender(
            httpx_client=self.mock_httpx_client, config_store=self.config_store
        )  # Corrected argument name

    def test_constructor_stores_client(self) -> None:
        self.assertEqual(self.notifier._client, self.mock_httpx_client)

    async def test_set_info_adds_new_config(self) -> None:
        task_id = 'task_new'
        config = create_sample_push_config(url='http://new.url/callback')

        await self.config_store.set_info(task_id, config)

        self.assertIn(task_id, self.config_store._push_notification_infos)
        self.assertEqual(
            self.config_store._push_notification_infos[task_id], [config]
        )

    async def test_set_info_appends_to_existing_config(self) -> None:
        task_id = 'task_update'
        initial_config = create_sample_push_config(
            url='http://initial.url/callback', config_id='cfg_initial'
        )
        await self.config_store.set_info(task_id, initial_config)

        updated_config = create_sample_push_config(
            url='http://updated.url/callback', config_id='cfg_updated'
        )
        await self.config_store.set_info(task_id, updated_config)

        self.assertIn(task_id, self.config_store._push_notification_infos)
        self.assertEqual(
            self.config_store._push_notification_infos[task_id][0],
            initial_config,
        )
        self.assertEqual(
            self.config_store._push_notification_infos[task_id][1],
            updated_config,
        )

    async def test_set_info_without_config_id(self) -> None:
        task_id = 'task1'
        initial_config = PushNotificationConfig(
            url='http://initial.url/callback'
        )
        await self.config_store.set_info(task_id, initial_config)

        assert (
            self.config_store._push_notification_infos[task_id][0].id == task_id
        )

        updated_config = PushNotificationConfig(
            url='http://initial.url/callback_new'
        )
        await self.config_store.set_info(task_id, updated_config)

        self.assertIn(task_id, self.config_store._push_notification_infos)
        assert len(self.config_store._push_notification_infos[task_id]) == 1
        self.assertEqual(
            self.config_store._push_notification_infos[task_id][0].url,
            updated_config.url,
        )

    async def test_get_info_existing_config(self) -> None:
        task_id = 'task_get_exist'
        config = create_sample_push_config(url='http://get.this/callback')
        await self.config_store.set_info(task_id, config)

        retrieved_config = await self.config_store.get_info(task_id)
        self.assertEqual(retrieved_config, [config])

    async def test_get_info_non_existent_config(self) -> None:
        task_id = 'task_get_non_exist'
        retrieved_config = await self.config_store.get_info(task_id)
        assert retrieved_config == []

    async def test_delete_info_existing_config(self) -> None:
        task_id = 'task_delete_exist'
        config = create_sample_push_config(url='http://delete.this/callback')
        await self.config_store.set_info(task_id, config)

        self.assertIn(task_id, self.config_store._push_notification_infos)
        await self.config_store.delete_info(task_id, config_id=config.id)
        self.assertNotIn(task_id, self.config_store._push_notification_infos)

    async def test_delete_info_non_existent_config(self) -> None:
        task_id = 'task_delete_non_exist'
        # Ensure it doesn't raise an error
        try:
            await self.config_store.delete_info(task_id)
        except Exception as e:
            self.fail(
                f'delete_info raised {e} unexpectedly for nonexistent task_id'
            )
        self.assertNotIn(
            task_id, self.config_store._push_notification_infos
        )  # Should still not be there

    async def test_send_notification_success(self) -> None:
        task_id = 'task_send_success'
        task_data = create_sample_task(task_id=task_id)
        config = create_sample_push_config(url='http://notify.me/here')
        await self.config_store.set_info(task_id, config)

        # Mock the post call to simulate success
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.notifier.send_notification(task_data)  # Pass only task_data

        self.mock_httpx_client.post.assert_awaited_once()
        called_args, called_kwargs = self.mock_httpx_client.post.call_args
        self.assertEqual(called_args[0], config.url)
        self.assertEqual(
            called_kwargs['json'],
            task_data.model_dump(mode='json', exclude_none=True),
        )
        self.assertNotIn(
            'auth', called_kwargs
        )  # auth is not passed by current implementation
        mock_response.raise_for_status.assert_called_once()

    async def test_send_notification_with_token_success(self) -> None:
        task_id = 'task_send_success'
        task_data = create_sample_task(task_id=task_id)
        config = create_sample_push_config(
            url='http://notify.me/here', token='unique_token'
        )
        await self.config_store.set_info(task_id, config)

        # Mock the post call to simulate success
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.notifier.send_notification(task_data)  # Pass only task_data

        self.mock_httpx_client.post.assert_awaited_once()
        called_args, called_kwargs = self.mock_httpx_client.post.call_args
        self.assertEqual(called_args[0], config.url)
        self.assertEqual(
            called_kwargs['json'],
            task_data.model_dump(mode='json', exclude_none=True),
        )
        self.assertEqual(
            called_kwargs['headers'],
            {'X-A2A-Notification-Token': 'unique_token'},
        )
        self.assertNotIn(
            'auth', called_kwargs
        )  # auth is not passed by current implementation
        mock_response.raise_for_status.assert_called_once()

    async def test_send_notification_no_config(self) -> None:
        task_id = 'task_send_no_config'
        task_data = create_sample_task(task_id=task_id)

        await self.notifier.send_notification(task_data)  # Pass only task_data

        self.mock_httpx_client.post.assert_not_called()

    @patch('a2a.server.tasks.base_push_notification_sender.logger')
    async def test_send_notification_http_status_error(
        self, mock_logger: MagicMock
    ) -> None:
        task_id = 'task_send_http_err'
        task_data = create_sample_task(task_id=task_id)
        config = create_sample_push_config(url='http://notify.me/http_error')
        await self.config_store.set_info(task_id, config)

        mock_response = MagicMock(
            spec=httpx.Response
        )  # Use MagicMock for status_code attribute
        mock_response.status_code = 404
        mock_response.text = 'Not Found'
        http_error = httpx.HTTPStatusError(
            'Not Found', request=MagicMock(), response=mock_response
        )
        self.mock_httpx_client.post.side_effect = http_error

        # The method should catch the error and log it, not re-raise
        await self.notifier.send_notification(task_data)  # Pass only task_data

        self.mock_httpx_client.post.assert_awaited_once()
        mock_logger.exception.assert_called_once()
        # Check that the error message contains the generic part and the specific exception string
        self.assertIn(
            'Error sending push-notification',
            mock_logger.exception.call_args[0][0],
        )

    @patch('a2a.server.tasks.base_push_notification_sender.logger')
    async def test_send_notification_request_error(
        self, mock_logger: MagicMock
    ) -> None:
        task_id = 'task_send_req_err'
        task_data = create_sample_task(task_id=task_id)
        config = create_sample_push_config(url='http://notify.me/req_error')
        await self.config_store.set_info(task_id, config)

        request_error = httpx.RequestError('Network issue', request=MagicMock())
        self.mock_httpx_client.post.side_effect = request_error

        await self.notifier.send_notification(task_data)  # Pass only task_data

        self.mock_httpx_client.post.assert_awaited_once()
        mock_logger.exception.assert_called_once()
        self.assertIn(
            'Error sending push-notification',
            mock_logger.exception.call_args[0][0],
        )

    @patch('a2a.server.tasks.base_push_notification_sender.logger')
    async def test_send_notification_with_auth(
        self, mock_logger: MagicMock
    ) -> None:
        task_id = 'task_send_auth'
        task_data = create_sample_task(task_id=task_id)
        auth_info = ('user', 'pass')
        config = create_sample_push_config(url='http://notify.me/auth')
        config.authentication = MagicMock()  # Mocking the structure for auth
        config.authentication.schemes = ['basic']  # Assume basic for simplicity
        config.authentication.credentials = (
            auth_info  # This might need to be a specific model
        )
        # For now, let's assume it's a tuple for basic auth
        # The actual PushNotificationAuthenticationInfo is more complex
        # For this test, we'll simplify and assume InMemoryPushNotifier
        # directly uses tuple for httpx's `auth` param if basic.
        # A more accurate test would construct the real auth model.
        # Given the current implementation of InMemoryPushNotifier,
        # it only supports basic auth via tuple.

        await self.config_store.set_info(task_id, config)

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.notifier.send_notification(task_data)  # Pass only task_data

        self.mock_httpx_client.post.assert_awaited_once()
        called_args, called_kwargs = self.mock_httpx_client.post.call_args
        self.assertEqual(called_args[0], config.url)
        self.assertEqual(
            called_kwargs['json'],
            task_data.model_dump(mode='json', exclude_none=True),
        )
        self.assertNotIn(
            'auth', called_kwargs
        )  # auth is not passed by current implementation
        mock_response.raise_for_status.assert_called_once()


if __name__ == '__main__':
    unittest.main()
