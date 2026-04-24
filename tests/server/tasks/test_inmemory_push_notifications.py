import unittest

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from google.protobuf.json_format import MessageToDict

from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.server.tasks.base_push_notification_sender import (
    BasePushNotificationSender,
)
from a2a.server.tasks.inmemory_push_notification_config_store import (
    InMemoryPushNotificationConfigStore,
)
from a2a.types.a2a_pb2 import (
    StreamResponse,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)


# Suppress logging for cleaner test output, can be enabled for debugging
# logging.disable(logging.CRITICAL)


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


class SampleUser(User):
    """A test implementation of the User interface."""

    def __init__(self, user_name: str):
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


MINIMAL_CALL_CONTEXT = ServerCallContext(user=SampleUser(user_name='user'))


class TestInMemoryPushNotifier(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.mock_httpx_client = AsyncMock(spec=httpx.AsyncClient)
        self.config_store = InMemoryPushNotificationConfigStore()
        self.notifier = BasePushNotificationSender(
            httpx_client=self.mock_httpx_client,
            config_store=self.config_store,
        )

    def test_constructor_stores_client(self) -> None:
        self.assertEqual(self.notifier._client, self.mock_httpx_client)

    async def test_set_info_adds_new_config(self) -> None:
        task_id = 'task_new'
        config = _create_sample_push_config(url='http://new.url/callback')

        await self.config_store.set_info(task_id, config, MINIMAL_CALL_CONTEXT)

        retrieved = await self.config_store.get_info(
            task_id, MINIMAL_CALL_CONTEXT
        )
        self.assertEqual(retrieved, [config])

    async def test_set_info_appends_to_existing_config(self) -> None:
        task_id = 'task_update'
        initial_config = _create_sample_push_config(
            url='http://initial.url/callback', config_id='cfg_initial'
        )
        await self.config_store.set_info(
            task_id, initial_config, MINIMAL_CALL_CONTEXT
        )

        updated_config = _create_sample_push_config(
            url='http://updated.url/callback', config_id='cfg_updated'
        )
        await self.config_store.set_info(
            task_id, updated_config, MINIMAL_CALL_CONTEXT
        )

        retrieved = await self.config_store.get_info(
            task_id, MINIMAL_CALL_CONTEXT
        )
        self.assertEqual(len(retrieved), 2)
        self.assertEqual(retrieved[0], initial_config)
        self.assertEqual(retrieved[1], updated_config)

    async def test_set_info_without_config_id(self) -> None:
        task_id = 'task1'
        initial_config = TaskPushNotificationConfig(
            url='http://initial.url/callback'
        )
        await self.config_store.set_info(
            task_id, initial_config, MINIMAL_CALL_CONTEXT
        )

        retrieved = await self.config_store.get_info(
            task_id, MINIMAL_CALL_CONTEXT
        )
        assert retrieved[0].id == task_id

        updated_config = TaskPushNotificationConfig(
            url='http://initial.url/callback_new'
        )
        await self.config_store.set_info(
            task_id, updated_config, MINIMAL_CALL_CONTEXT
        )

        retrieved = await self.config_store.get_info(
            task_id, MINIMAL_CALL_CONTEXT
        )
        assert len(retrieved) == 1
        self.assertEqual(retrieved[0].url, updated_config.url)

    async def test_get_info_existing_config(self) -> None:
        task_id = 'task_get_exist'
        config = _create_sample_push_config(url='http://get.this/callback')
        await self.config_store.set_info(task_id, config, MINIMAL_CALL_CONTEXT)

        retrieved_config = await self.config_store.get_info(
            task_id, MINIMAL_CALL_CONTEXT
        )
        self.assertEqual(retrieved_config, [config])

    async def test_get_info_non_existent_config(self) -> None:
        task_id = 'task_get_non_exist'
        retrieved_config = await self.config_store.get_info(
            task_id, MINIMAL_CALL_CONTEXT
        )
        assert retrieved_config == []

    async def test_delete_info_existing_config(self) -> None:
        task_id = 'task_delete_exist'
        config = _create_sample_push_config(url='http://delete.this/callback')
        await self.config_store.set_info(task_id, config, MINIMAL_CALL_CONTEXT)

        retrieved = await self.config_store.get_info(
            task_id, MINIMAL_CALL_CONTEXT
        )
        self.assertEqual(len(retrieved), 1)

        await self.config_store.delete_info(
            task_id, config_id=config.id, context=MINIMAL_CALL_CONTEXT
        )
        retrieved = await self.config_store.get_info(
            task_id, MINIMAL_CALL_CONTEXT
        )
        self.assertEqual(len(retrieved), 0)

    async def test_delete_info_non_existent_config(self) -> None:
        task_id = 'task_delete_non_exist'
        # Ensure it doesn't raise an error
        try:
            await self.config_store.delete_info(
                task_id, context=MINIMAL_CALL_CONTEXT
            )
        except Exception as e:
            self.fail(
                f'delete_info raised {e} unexpectedly for nonexistent task_id'
            )
        retrieved = await self.config_store.get_info(
            task_id, MINIMAL_CALL_CONTEXT
        )
        self.assertEqual(len(retrieved), 0)

    async def test_send_notification_success(self) -> None:
        task_id = 'task_send_success'
        task_data = _create_sample_task(task_id=task_id)
        config = _create_sample_push_config(url='http://notify.me/here')
        await self.config_store.set_info(task_id, config, MINIMAL_CALL_CONTEXT)

        # Mock the post call to simulate success
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.notifier.send_notification(task_id, task_data)

        self.mock_httpx_client.post.assert_awaited_once()
        called_args, called_kwargs = self.mock_httpx_client.post.call_args
        self.assertEqual(called_args[0], config.url)
        self.assertEqual(
            called_kwargs['json'],
            MessageToDict(StreamResponse(task=task_data)),
        )
        self.assertNotIn(
            'auth', called_kwargs
        )  # auth is not passed by current implementation
        mock_response.raise_for_status.assert_called_once()

    async def test_send_notification_with_token_success(self) -> None:
        task_id = 'task_send_success'
        task_data = _create_sample_task(task_id=task_id)
        config = _create_sample_push_config(
            url='http://notify.me/here', token='unique_token'
        )
        await self.config_store.set_info(task_id, config, MINIMAL_CALL_CONTEXT)

        # Mock the post call to simulate success
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.notifier.send_notification(task_id, task_data)

        self.mock_httpx_client.post.assert_awaited_once()
        called_args, called_kwargs = self.mock_httpx_client.post.call_args
        self.assertEqual(called_args[0], config.url)
        self.assertEqual(
            called_kwargs['json'],
            MessageToDict(StreamResponse(task=task_data)),
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
        task_data = _create_sample_task(task_id=task_id)

        await self.notifier.send_notification(task_id, task_data)

        self.mock_httpx_client.post.assert_not_called()

    @patch('a2a.server.tasks.base_push_notification_sender.logger')
    async def test_send_notification_http_status_error(
        self, mock_logger: MagicMock
    ) -> None:
        task_id = 'task_send_http_err'
        task_data = _create_sample_task(task_id=task_id)
        config = _create_sample_push_config(url='http://notify.me/http_error')
        await self.config_store.set_info(task_id, config, MINIMAL_CALL_CONTEXT)

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
        await self.notifier.send_notification(task_id, task_data)

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
        task_data = _create_sample_task(task_id=task_id)
        config = _create_sample_push_config(url='http://notify.me/req_error')
        await self.config_store.set_info(task_id, config, MINIMAL_CALL_CONTEXT)

        request_error = httpx.RequestError('Network issue', request=MagicMock())
        self.mock_httpx_client.post.side_effect = request_error

        await self.notifier.send_notification(task_id, task_data)

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
        """Test that auth field is not used by current implementation.

        The current BasePushNotificationSender only supports token-based auth,
        not the authentication field. This test verifies that the notification
        still works even if the config has an authentication field set.
        """
        task_id = 'task_send_auth'
        task_data = _create_sample_task(task_id=task_id)
        config = _create_sample_push_config(url='http://notify.me/auth')
        # The current implementation doesn't use the authentication field
        # It only supports token-based auth via the token field
        await self.config_store.set_info(task_id, config, MINIMAL_CALL_CONTEXT)

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        await self.notifier.send_notification(task_id, task_data)

        self.mock_httpx_client.post.assert_awaited_once()
        called_args, called_kwargs = self.mock_httpx_client.post.call_args
        self.assertEqual(called_args[0], config.url)
        self.assertEqual(
            called_kwargs['json'],
            MessageToDict(StreamResponse(task=task_data)),
        )
        self.assertNotIn(
            'auth', called_kwargs
        )  # auth is not passed by current implementation
        mock_response.raise_for_status.assert_called_once()

    async def test_owner_resource_scoping(self) -> None:
        """Test that operations are scoped to the correct owner."""
        context_user1 = ServerCallContext(user=SampleUser(user_name='user1'))
        context_user2 = ServerCallContext(user=SampleUser(user_name='user2'))

        # Create configs for different owners
        task1_u1_config1 = TaskPushNotificationConfig(
            id='t1-u1-c1', url='http://u1.com/1'
        )
        task1_u1_config2 = TaskPushNotificationConfig(
            id='t1-u1-c2', url='http://u1.com/2'
        )
        task1_u2_config1 = TaskPushNotificationConfig(
            id='t1-u2-c1', url='http://u2.com/1'
        )
        task2_u1_config1 = TaskPushNotificationConfig(
            id='t2-u1-c1', url='http://u1.com/3'
        )

        await self.config_store.set_info(
            'task1', task1_u1_config1, context_user1
        )
        await self.config_store.set_info(
            'task1', task1_u1_config2, context_user1
        )
        await self.config_store.set_info(
            'task1', task1_u2_config1, context_user2
        )
        await self.config_store.set_info(
            'task2', task2_u1_config1, context_user1
        )

        # Test GET_INFO
        # User 1 should get only their configs for task1
        u1_task1_configs = await self.config_store.get_info(
            'task1', context_user1
        )
        self.assertEqual(len(u1_task1_configs), 2)
        self.assertEqual(
            {c.id for c in u1_task1_configs}, {'t1-u1-c1', 't1-u1-c2'}
        )

        # User 2 should get only their configs for task1
        u2_task1_configs = await self.config_store.get_info(
            'task1', context_user2
        )
        self.assertEqual(len(u2_task1_configs), 1)
        self.assertEqual(u2_task1_configs[0].id, 't1-u2-c1')

        # User 2 should get no configs for task2
        u2_task2_configs = await self.config_store.get_info(
            'task2', context_user2
        )
        self.assertEqual(len(u2_task2_configs), 0)

        # User 1 should get their config for task2
        u1_task2_configs = await self.config_store.get_info(
            'task2', context_user1
        )
        self.assertEqual(len(u1_task2_configs), 1)
        self.assertEqual(u1_task2_configs[0].id, 't2-u1-c1')

        # Test DELETE_INFO
        # User 2 deleting User 1's config should not work
        await self.config_store.delete_info('task1', context_user2, 't1-u1-c1')
        u1_task1_configs = await self.config_store.get_info(
            'task1', context_user1
        )
        self.assertEqual(len(u1_task1_configs), 2)

        # User 1 deleting their own config
        await self.config_store.delete_info('task1', context_user1, 't1-u1-c1')
        u1_task1_configs = await self.config_store.get_info(
            'task1', context_user1
        )
        self.assertEqual(len(u1_task1_configs), 1)
        self.assertEqual(u1_task1_configs[0].id, 't1-u1-c2')

        # User 1 deleting all configs for task2
        await self.config_store.delete_info('task2', context=context_user1)
        u1_task2_configs = await self.config_store.get_info(
            'task2', context_user1
        )
        self.assertEqual(len(u1_task2_configs), 0)

        # Cleanup remaining
        await self.config_store.delete_info('task1', context=context_user1)
        await self.config_store.delete_info('task1', context=context_user2)


class TestPushNotificationDispatchAcrossOwners(
    unittest.IsolatedAsyncioTestCase
):
    """Dispatch-correctness tests for the registrar/dispatcher asymmetry.

    Push notifications must fire for any event on the task, regardless of
    which user's action triggered the event. The dispatch path therefore
    reads configs via get_info_for_dispatch (cross-owner), not
    get_info (owner-scoped).
    """

    def setUp(self) -> None:
        self.mock_httpx_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        self.mock_httpx_client.post.return_value = mock_response

        self.config_store = InMemoryPushNotificationConfigStore()

        self.sender = BasePushNotificationSender(
            httpx_client=self.mock_httpx_client,
            config_store=self.config_store,
        )

    async def test_multi_registrar_fan_out(self) -> None:
        """Three users registering distinct webhooks for the same task all fire."""
        users_and_urls = [
            ('alice', 'http://alice.example.com/cb', 'tok-alice'),
            ('bob', 'http://bob.example.com/cb', 'tok-bob'),
            ('carol', 'http://carol.example.com/cb', 'tok-carol'),
        ]
        for user_name, url, token in users_and_urls:
            ctx = ServerCallContext(user=SampleUser(user_name=user_name))
            cfg = TaskPushNotificationConfig(
                id=f'cfg-{user_name}', url=url, token=token
            )
            await self.config_store.set_info('shared-task', cfg, ctx)

        await self.sender.send_notification(
            'shared-task', _create_sample_task(task_id='shared-task')
        )

        self.assertEqual(self.mock_httpx_client.post.await_count, 3)
        called_urls = {
            call.args[0] for call in self.mock_httpx_client.post.call_args_list
        }
        self.assertEqual(
            called_urls,
            {url for _, url, _ in users_and_urls},
        )
        called_tokens = {
            call.kwargs['headers']['X-A2A-Notification-Token']
            for call in self.mock_httpx_client.post.call_args_list
        }
        self.assertEqual(
            called_tokens,
            {token for _, _, token in users_and_urls},
        )

    async def test_write_side_owner_isolation_preserved(self) -> None:
        """Bob's ``delete_info`` against Alice's config is a no-op.

        After the no-op, Alice's config must still be:
        (a) retrievable via the user-callable ``get_info`` for Alice, and
        (b) returned by ``get_info_for_dispatch`` so that the
            notification will still fire.

        Guards the write-side scoping that the design preserves
        (see §9.3).
        """
        alice_ctx = ServerCallContext(user=SampleUser(user_name='alice'))
        bob_ctx = ServerCallContext(user=SampleUser(user_name='bob'))

        config = TaskPushNotificationConfig(
            id='alice-cfg',
            url='http://alice.example.com/cb',
            token='alice-token',
        )
        await self.config_store.set_info('shared-task', config, alice_ctx)

        # Bob attempts to delete Alice's config -- must be a no-op.
        await self.config_store.delete_info(
            'shared-task', context=bob_ctx, config_id='alice-cfg'
        )

        # (a) Alice's user-callable view is unchanged.
        alice_view = await self.config_store.get_info('shared-task', alice_ctx)
        self.assertEqual(len(alice_view), 1)
        self.assertEqual(alice_view[0].id, 'alice-cfg')

        # (b) Dispatch path still sees the config (notifications fire).
        dispatched = await self.config_store.get_info_for_dispatch(
            'shared-task'
        )
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0].id, 'alice-cfg')
        self.assertEqual(dispatched[0].token, 'alice-token')

        # And end-to-end: the sender actually dispatches to Alice's URL.
        await self.sender.send_notification(
            'shared-task', _create_sample_task(task_id='shared-task')
        )
        self.mock_httpx_client.post.assert_awaited_once_with(
            'http://alice.example.com/cb',
            json=MessageToDict(
                StreamResponse(task=_create_sample_task(task_id='shared-task'))
            ),
            headers={'X-A2A-Notification-Token': 'alice-token'},
        )

    async def test_cross_user_dispatch_alice_registers_bob_triggers(
        self,
    ) -> None:
        """Alice registers; Bob triggers; Alice's webhook receives the POST.

        The send_notification carries no identity, so there is no notion of
        "who triggered this event" at the store layer. get_info_for_dispatch
        returns Alice's config because Alice registered it. The fact that the
        event was caused by Bob is not visible to (and not relevant for) the
        dispatch path.
        """
        alice_context = ServerCallContext(user=SampleUser(user_name='alice'))
        config = _create_sample_push_config(
            url='http://alice.example.com/cb', token='alice-token'
        )
        await self.config_store.set_info('collab-task', config, alice_context)

        # No bob_context is passed anywhere -- the dispatch path never
        # sees it. This is precisely the point: identity is not the
        # dispatch path's concern.
        await self.sender.send_notification(
            'collab-task', _create_sample_task(task_id='collab-task')
        )

        self.mock_httpx_client.post.assert_awaited_once_with(
            'http://alice.example.com/cb',
            json=MessageToDict(
                StreamResponse(task=_create_sample_task(task_id='collab-task'))
            ),
            headers={'X-A2A-Notification-Token': 'alice-token'},
        )


if __name__ == '__main__':
    unittest.main()
