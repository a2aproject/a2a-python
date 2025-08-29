import unittest
import unittest.async_case
from collections.abc import AsyncGenerator
from typing import Any, NoReturn
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.agent_execution.request_context_builder import (
    RequestContextBuilder,
)
from a2a.server.context import ServerCallContext
from a2a.server.events import QueueManager
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler, JSONRPCHandler
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    PushNotificationConfigStore,
    PushNotificationSender,
    TaskStore,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    Artifact,
    AuthenticatedExtendedCardNotConfiguredError,
    CancelTaskRequest,
    CancelTaskSuccessResponse,
    DeleteTaskPushNotificationConfigParams,
    DeleteTaskPushNotificationConfigRequest,
    DeleteTaskPushNotificationConfigSuccessResponse,
    GetAuthenticatedExtendedCardRequest,
    GetAuthenticatedExtendedCardResponse,
    GetAuthenticatedExtendedCardSuccessResponse,
    GetTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigResponse,
    GetTaskPushNotificationConfigSuccessResponse,
    GetTaskRequest,
    GetTaskResponse,
    GetTaskSuccessResponse,
    InternalError,
    JSONRPCErrorResponse,
    ListTaskPushNotificationConfigParams,
    ListTaskPushNotificationConfigRequest,
    ListTaskPushNotificationConfigSuccessResponse,
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Part,
    PushNotificationConfig,
    SendMessageRequest,
    SendMessageSuccessResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageSuccessResponse,
    SetTaskPushNotificationConfigRequest,
    SetTaskPushNotificationConfigResponse,
    SetTaskPushNotificationConfigSuccessResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskNotFoundError,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskResubscriptionRequest,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils.errors import ServerError

MINIMAL_TASK: dict[str, Any] = {
    'id': 'task_123',
    'contextId': 'session-xyz',
    'status': {'state': 'submitted'},
    'kind': 'task',
}
MESSAGE_PAYLOAD: dict[str, Any] = {
    'role': 'agent',
    'parts': [{'text': 'test message'}],
    'messageId': '111',
}


class TestJSONRPCtHandler(unittest.async_case.IsolatedAsyncioTestCase):
    @pytest.fixture(autouse=True)
    def init_fixtures(self) -> None:
        self.mock_agent_card = MagicMock(
            spec=AgentCard,
            url='http://agent.example.com/api',
            supports_authenticated_extended_card=True,
        )

    async def test_on_get_task_success(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        call_context = ServerCallContext(state={'foo': 'bar'})
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        task_id = 'test_task_id'
        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task
        request = GetTaskRequest(id='1', params=TaskQueryParams(id=task_id))
        response: GetTaskResponse = await handler.on_get_task(
            request, call_context
        )
        self.assertIsInstance(response.root, GetTaskSuccessResponse)
        assert response.root.result == mock_task  # type: ignore
        mock_task_store.get.assert_called_once_with(task_id)

    async def test_on_get_task_not_found(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task_store.get.return_value = None
        request = GetTaskRequest(
            id='1',
            method='tasks/get',
            params=TaskQueryParams(id='nonexistent_id'),
        )
        call_context = ServerCallContext(state={'foo': 'bar'})
        response: GetTaskResponse = await handler.on_get_task(
            request, call_context
        )
        self.assertIsInstance(response.root, JSONRPCErrorResponse)
        assert response.root.error == TaskNotFoundError()  # type: ignore

    async def test_on_cancel_task_success(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        task_id = 'test_task_id'
        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task
        mock_agent_executor.cancel.return_value = None
        call_context = ServerCallContext(state={'foo': 'bar'})

        async def streaming_coro():
            yield mock_task

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            request = CancelTaskRequest(id='1', params=TaskIdParams(id=task_id))
            response = await handler.on_cancel_task(request, call_context)
            assert mock_agent_executor.cancel.call_count == 1
            self.assertIsInstance(response.root, CancelTaskSuccessResponse)
            assert response.root.result == mock_task  # type: ignore
            mock_agent_executor.cancel.assert_called_once()

    async def test_on_cancel_task_not_supported(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        task_id = 'test_task_id'
        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task
        mock_agent_executor.cancel.return_value = None
        call_context = ServerCallContext(state={'foo': 'bar'})

        async def streaming_coro():
            raise ServerError(UnsupportedOperationError())
            yield

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            request = CancelTaskRequest(id='1', params=TaskIdParams(id=task_id))
            response = await handler.on_cancel_task(request, call_context)
            assert mock_agent_executor.cancel.call_count == 1
            self.assertIsInstance(response.root, JSONRPCErrorResponse)
            assert response.root.error == UnsupportedOperationError()  # type: ignore
            mock_agent_executor.cancel.assert_called_once()

    async def test_on_cancel_task_not_found(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task_store.get.return_value = None
        request = CancelTaskRequest(
            id='1',
            method='tasks/cancel',
            params=TaskIdParams(id='nonexistent_id'),
        )
        response = await handler.on_cancel_task(request)
        self.assertIsInstance(response.root, JSONRPCErrorResponse)
        assert response.root.error == TaskNotFoundError()  # type: ignore
        mock_task_store.get.assert_called_once_with('nonexistent_id')
        mock_agent_executor.cancel.assert_not_called()

    @patch(
        'a2a.server.agent_execution.simple_request_context_builder.SimpleRequestContextBuilder.build'
    )
    async def test_on_message_new_message_success(
        self, _mock_builder_build: AsyncMock
    ) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task
        mock_agent_executor.execute.return_value = None

        _mock_builder_build.return_value = RequestContext(
            request=MagicMock(),
            task_id='task_123',
            context_id='session-xyz',
            task=None,
            related_tasks=None,
        )

        async def streaming_coro():
            yield mock_task

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            request = SendMessageRequest(
                id='1',
                params=MessageSendParams(message=Message(**MESSAGE_PAYLOAD)),
            )
            response = await handler.on_message_send(request)
            assert mock_agent_executor.execute.call_count == 1
            self.assertIsInstance(response.root, SendMessageSuccessResponse)
            assert response.root.result == mock_task  # type: ignore
            mock_agent_executor.execute.assert_called_once()

    async def test_on_message_new_message_with_existing_task_success(
        self,
    ) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task
        mock_agent_executor.execute.return_value = None

        async def streaming_coro():
            yield mock_task

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            request = SendMessageRequest(
                id='1',
                params=MessageSendParams(
                    message=Message(
                        **MESSAGE_PAYLOAD,
                        task_id=mock_task.id,
                        context_id=mock_task.context_id,
                    )
                ),
            )
            response = await handler.on_message_send(request)
            assert mock_agent_executor.execute.call_count == 1
            self.assertIsInstance(response.root, SendMessageSuccessResponse)
            assert response.root.result == mock_task  # type: ignore
            mock_agent_executor.execute.assert_called_once()

    async def test_on_message_error(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task_store.get.return_value = None
        mock_agent_executor.execute.return_value = None

        async def streaming_coro():
            raise ServerError(error=UnsupportedOperationError())
            yield

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            request = SendMessageRequest(
                id='1',
                params=MessageSendParams(
                    message=Message(
                        **MESSAGE_PAYLOAD,
                    )
                ),
            )
            response = await handler.on_message_send(request)

            self.assertIsInstance(response.root, JSONRPCErrorResponse)
            assert response.root.error == UnsupportedOperationError()  # type: ignore
            mock_agent_executor.execute.assert_called_once()

    @patch(
        'a2a.server.agent_execution.simple_request_context_builder.SimpleRequestContextBuilder.build'
    )
    async def test_on_message_stream_new_message_success(
        self, _mock_builder_build: AsyncMock
    ) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )

        self.mock_agent_card.capabilities = AgentCapabilities(streaming=True)
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        _mock_builder_build.return_value = RequestContext(
            request=MagicMock(),
            task_id='task_123',
            context_id='session-xyz',
            task=None,
            related_tasks=None,
        )

        events: list[Any] = [
            Task(**MINIMAL_TASK),
            TaskArtifactUpdateEvent(
                task_id='task_123',
                context_id='session-xyz',
                artifact=Artifact(
                    artifact_id='11', parts=[Part(TextPart(text='text'))]
                ),
            ),
            TaskStatusUpdateEvent(
                task_id='task_123',
                context_id='session-xyz',
                status=TaskStatus(state=TaskState.completed),
                final=True,
            ),
        ]

        async def streaming_coro():
            for event in events:
                yield event

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            mock_task_store.get.return_value = None
            mock_agent_executor.execute.return_value = None
            request = SendStreamingMessageRequest(
                id='1',
                params=MessageSendParams(message=Message(**MESSAGE_PAYLOAD)),
            )
            response = handler.on_message_send_stream(request)
            assert isinstance(response, AsyncGenerator)
            collected_events: list[Any] = []
            async for event in response:
                collected_events.append(event)
            assert len(collected_events) == len(events)
            for i, event in enumerate(collected_events):
                assert isinstance(
                    event.root, SendStreamingMessageSuccessResponse
                )
                assert event.root.result == events[i]
            mock_agent_executor.execute.assert_called_once()

    async def test_on_message_stream_new_message_existing_task_success(
        self,
    ) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )

        self.mock_agent_card.capabilities = AgentCapabilities(streaming=True)

        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task = Task(**MINIMAL_TASK, history=[])
        events: list[Any] = [
            mock_task,
            TaskArtifactUpdateEvent(
                task_id='task_123',
                context_id='session-xyz',
                artifact=Artifact(
                    artifact_id='11', parts=[Part(TextPart(text='text'))]
                ),
            ),
            TaskStatusUpdateEvent(
                task_id='task_123',
                context_id='session-xyz',
                status=TaskStatus(state=TaskState.working),
                final=True,
            ),
        ]

        async def streaming_coro():
            for event in events:
                yield event

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            mock_task_store.get.return_value = mock_task
            mock_agent_executor.execute.return_value = None
            request = SendStreamingMessageRequest(
                id='1',
                params=MessageSendParams(
                    message=Message(
                        **MESSAGE_PAYLOAD,
                        task_id=mock_task.id,
                        context_id=mock_task.context_id,
                    )
                ),
            )
            response = handler.on_message_send_stream(request)
            assert isinstance(response, AsyncGenerator)
            collected_events = [item async for item in response]
            assert len(collected_events) == len(events)
            mock_agent_executor.execute.assert_called_once()
            assert mock_task.history is not None and len(mock_task.history) == 1

    async def test_set_push_notification_success(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        mock_push_notification_store = AsyncMock(
            spec=PushNotificationConfigStore
        )

        request_handler = DefaultRequestHandler(
            mock_agent_executor,
            mock_task_store,
            push_config_store=mock_push_notification_store,
        )
        self.mock_agent_card.capabilities = AgentCapabilities(
            streaming=True, push_notifications=True
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task
        task_push_config = TaskPushNotificationConfig(
            task_id=mock_task.id,
            push_notification_config=PushNotificationConfig(
                url='http://example.com'
            ),
        )
        request = SetTaskPushNotificationConfigRequest(
            id='1', params=task_push_config
        )
        response: SetTaskPushNotificationConfigResponse = (
            await handler.set_push_notification_config(request)
        )
        self.assertIsInstance(
            response.root, SetTaskPushNotificationConfigSuccessResponse
        )
        assert response.root.result == task_push_config  # type: ignore
        mock_push_notification_store.set_info.assert_called_once_with(
            mock_task.id, task_push_config.push_notification_config
        )

    async def test_get_push_notification_success(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        push_notification_store = InMemoryPushNotificationConfigStore()
        request_handler = DefaultRequestHandler(
            mock_agent_executor,
            mock_task_store,
            push_config_store=push_notification_store,
        )
        self.mock_agent_card.capabilities = AgentCapabilities(
            streaming=True, push_notifications=True
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task
        task_push_config = TaskPushNotificationConfig(
            task_id=mock_task.id,
            push_notification_config=PushNotificationConfig(
                url='http://example.com'
            ),
        )
        request = SetTaskPushNotificationConfigRequest(
            id='1', params=task_push_config
        )
        await handler.set_push_notification_config(request)

        get_request: GetTaskPushNotificationConfigRequest = (
            GetTaskPushNotificationConfigRequest(
                id='1', params=TaskIdParams(id=mock_task.id)
            )
        )
        get_response: GetTaskPushNotificationConfigResponse = (
            await handler.get_push_notification_config(get_request)
        )
        self.assertIsInstance(
            get_response.root, GetTaskPushNotificationConfigSuccessResponse
        )
        assert get_response.root.result == task_push_config  # type: ignore

    @patch(
        'a2a.server.agent_execution.simple_request_context_builder.SimpleRequestContextBuilder.build'
    )
    async def test_on_message_stream_new_message_send_push_notification_success(
        self, _mock_builder_build: AsyncMock
    ) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        mock_httpx_client = AsyncMock(spec=httpx.AsyncClient)
        push_notification_store = InMemoryPushNotificationConfigStore()
        push_notification_sender = BasePushNotificationSender(
            mock_httpx_client, push_notification_store
        )
        request_handler = DefaultRequestHandler(
            mock_agent_executor,
            mock_task_store,
            push_config_store=push_notification_store,
            push_sender=push_notification_sender,
        )
        self.mock_agent_card.capabilities = AgentCapabilities(
            streaming=True, push_notifications=True
        )
        _mock_builder_build.return_value = RequestContext(
            request=MagicMock(),
            task_id='task_123',
            context_id='session-xyz',
            task=None,
            related_tasks=None,
        )

        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        events: list[Any] = [
            Task(**MINIMAL_TASK),
            TaskArtifactUpdateEvent(
                task_id='task_123',
                context_id='session-xyz',
                artifact=Artifact(
                    artifact_id='11', parts=[Part(TextPart(text='text'))]
                ),
            ),
            TaskStatusUpdateEvent(
                task_id='task_123',
                context_id='session-xyz',
                status=TaskStatus(state=TaskState.completed),
                final=True,
            ),
        ]

        async def streaming_coro():
            for event in events:
                yield event

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            mock_task_store.get.return_value = None
            mock_agent_executor.execute.return_value = None
            mock_httpx_client.post.return_value = httpx.Response(200)
            request = SendStreamingMessageRequest(
                id='1',
                params=MessageSendParams(message=Message(**MESSAGE_PAYLOAD)),
            )
            request.params.configuration = MessageSendConfiguration(
                accepted_output_modes=['text'],
                push_notification_config=PushNotificationConfig(
                    url='http://example.com'
                ),
            )
            response = handler.on_message_send_stream(request)
            assert isinstance(response, AsyncGenerator)

            collected_events = [item async for item in response]
            assert len(collected_events) == len(events)

            calls = [
                call(
                    'http://example.com',
                    json={
                        'contextId': 'session-xyz',
                        'id': 'task_123',
                        'kind': 'task',
                        'status': {'state': 'submitted'},
                    },
                    headers=None,
                ),
                call(
                    'http://example.com',
                    json={
                        'artifacts': [
                            {
                                'artifactId': '11',
                                'parts': [
                                    {
                                        'kind': 'text',
                                        'text': 'text',
                                    }
                                ],
                            }
                        ],
                        'contextId': 'session-xyz',
                        'id': 'task_123',
                        'kind': 'task',
                        'status': {'state': 'submitted'},
                    },
                    headers=None,
                ),
                call(
                    'http://example.com',
                    json={
                        'artifacts': [
                            {
                                'artifactId': '11',
                                'parts': [
                                    {
                                        'kind': 'text',
                                        'text': 'text',
                                    }
                                ],
                            }
                        ],
                        'contextId': 'session-xyz',
                        'id': 'task_123',
                        'kind': 'task',
                        'status': {'state': 'completed'},
                    },
                    headers=None,
                ),
            ]
            mock_httpx_client.post.assert_has_calls(calls)

    async def test_on_resubscribe_existing_task_success(
        self,
    ) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        mock_queue_manager = AsyncMock(spec=QueueManager)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store, mock_queue_manager
        )
        self.mock_agent_card = MagicMock(spec=AgentCard)
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task = Task(**MINIMAL_TASK, history=[])
        events: list[Any] = [
            TaskArtifactUpdateEvent(
                task_id='task_123',
                context_id='session-xyz',
                artifact=Artifact(
                    artifact_id='11', parts=[Part(TextPart(text='text'))]
                ),
            ),
            TaskStatusUpdateEvent(
                task_id='task_123',
                context_id='session-xyz',
                status=TaskStatus(state=TaskState.completed),
                final=True,
            ),
        ]

        async def streaming_coro():
            for event in events:
                yield event

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            mock_task_store.get.return_value = mock_task
            mock_queue_manager.tap.return_value = EventQueue()
            request = TaskResubscriptionRequest(
                id='1', params=TaskIdParams(id=mock_task.id)
            )
            response = handler.on_resubscribe_to_task(request)
            assert isinstance(response, AsyncGenerator)
            collected_events: list[Any] = []
            async for event in response:
                collected_events.append(event)
            assert len(collected_events) == len(events)
            assert mock_task.history is not None and len(mock_task.history) == 0

    async def test_on_resubscribe_no_existing_task_error(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task_store.get.return_value = None
        request = TaskResubscriptionRequest(
            id='1', params=TaskIdParams(id='nonexistent_id')
        )
        response = handler.on_resubscribe_to_task(request)
        assert isinstance(response, AsyncGenerator)
        collected_events: list[Any] = []
        async for event in response:
            collected_events.append(event)
        assert len(collected_events) == 1
        self.assertIsInstance(collected_events[0].root, JSONRPCErrorResponse)
        assert collected_events[0].root.error == TaskNotFoundError()

    async def test_streaming_not_supported_error(
        self,
    ) -> None:
        """Test that on_message_send_stream raises an error when streaming not supported."""
        # Arrange
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        # Create agent card with streaming capability disabled
        self.mock_agent_card.capabilities = AgentCapabilities(streaming=False)
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)

        # Act & Assert
        request = SendStreamingMessageRequest(
            id='1',
            params=MessageSendParams(message=Message(**MESSAGE_PAYLOAD)),
        )

        # Should raise ServerError about streaming not supported
        with self.assertRaises(ServerError) as context:
            async for _ in handler.on_message_send_stream(request):
                pass

        self.assertEqual(
            str(context.exception.error.message),  # type: ignore
            'Streaming is not supported by the agent',
        )

    async def test_push_notifications_not_supported_error(self) -> None:
        """Test that set_push_notification raises an error when push notifications not supported."""
        # Arrange
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        # Create agent card with push notifications capability disabled
        self.mock_agent_card.capabilities = AgentCapabilities(
            push_notifications=False, streaming=True
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)

        # Act & Assert
        task_push_config = TaskPushNotificationConfig(
            task_id='task_123',
            push_notification_config=PushNotificationConfig(
                url='http://example.com'
            ),
        )
        request = SetTaskPushNotificationConfigRequest(
            id='1', params=task_push_config
        )

        # Should raise ServerError about push notifications not supported
        with self.assertRaises(ServerError) as context:
            await handler.set_push_notification_config(request)

        self.assertEqual(
            str(context.exception.error.message),  # type: ignore
            'Push notifications are not supported by the agent',
        )

    async def test_on_get_push_notification_no_push_config_store(self) -> None:
        """Test get_push_notification with no push notifier configured."""
        # Arrange
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        # Create request handler without a push notifier
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        self.mock_agent_card.capabilities = AgentCapabilities(
            push_notifications=True
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)

        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task

        # Act
        get_request = GetTaskPushNotificationConfigRequest(
            id='1', params=TaskIdParams(id=mock_task.id)
        )
        response = await handler.get_push_notification_config(get_request)

        # Assert
        self.assertIsInstance(response.root, JSONRPCErrorResponse)
        self.assertEqual(response.root.error, UnsupportedOperationError())  # type: ignore

    async def test_on_set_push_notification_no_push_config_store(self) -> None:
        """Test set_push_notification with no push notifier configured."""
        # Arrange
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        # Create request handler without a push notifier
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        self.mock_agent_card.capabilities = AgentCapabilities(
            push_notifications=True
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)

        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task

        # Act
        task_push_config = TaskPushNotificationConfig(
            task_id=mock_task.id,
            push_notification_config=PushNotificationConfig(
                url='http://example.com'
            ),
        )
        request = SetTaskPushNotificationConfigRequest(
            id='1', params=task_push_config
        )
        response = await handler.set_push_notification_config(request)

        # Assert
        self.assertIsInstance(response.root, JSONRPCErrorResponse)
        self.assertEqual(response.root.error, UnsupportedOperationError())  # type: ignore

    async def test_on_message_send_internal_error(self) -> None:
        """Test on_message_send with an internal error."""
        # Arrange
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)

        # Make the request handler raise an Internal error without specifying an error type
        async def raise_server_error(*args, **kwargs) -> NoReturn:
            raise ServerError(InternalError(message='Internal Error'))

        # Patch the method to raise an error
        with patch.object(
            request_handler, 'on_message_send', side_effect=raise_server_error
        ):
            # Act
            request = SendMessageRequest(
                id='1',
                params=MessageSendParams(message=Message(**MESSAGE_PAYLOAD)),
            )
            response = await handler.on_message_send(request)

            # Assert
            self.assertIsInstance(response.root, JSONRPCErrorResponse)
            self.assertIsInstance(response.root.error, InternalError)  # type: ignore

    async def test_on_message_stream_internal_error(self) -> None:
        """Test on_message_send_stream with an internal error."""
        # Arrange
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        self.mock_agent_card.capabilities = AgentCapabilities(streaming=True)
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)

        # Make the request handler raise an Internal error without specifying an error type
        async def raise_server_error(*args, **kwargs):
            raise ServerError(InternalError(message='Internal Error'))
            yield  # Need this to make it an async generator

        # Patch the method to raise an error
        with patch.object(
            request_handler,
            'on_message_send_stream',
            return_value=raise_server_error(),
        ):
            # Act
            request = SendStreamingMessageRequest(
                id='1',
                params=MessageSendParams(message=Message(**MESSAGE_PAYLOAD)),
            )

            # Get the single error response
            responses = []
            async for response in handler.on_message_send_stream(request):
                responses.append(response)

            # Assert
            self.assertEqual(len(responses), 1)
            self.assertIsInstance(responses[0].root, JSONRPCErrorResponse)
            self.assertIsInstance(responses[0].root.error, InternalError)

    async def test_default_request_handler_with_custom_components(self) -> None:
        """Test DefaultRequestHandler initialization with custom components."""
        # Arrange
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        mock_queue_manager = AsyncMock(spec=QueueManager)
        mock_push_config_store = AsyncMock(spec=PushNotificationConfigStore)
        mock_push_sender = AsyncMock(spec=PushNotificationSender)
        mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

        # Act
        handler = DefaultRequestHandler(
            agent_executor=mock_agent_executor,
            task_store=mock_task_store,
            queue_manager=mock_queue_manager,
            push_config_store=mock_push_config_store,
            push_sender=mock_push_sender,
            request_context_builder=mock_request_context_builder,
        )

        # Assert
        self.assertEqual(handler.agent_executor, mock_agent_executor)
        self.assertEqual(handler.task_store, mock_task_store)
        self.assertEqual(handler._queue_manager, mock_queue_manager)
        self.assertEqual(handler._push_config_store, mock_push_config_store)
        self.assertEqual(handler._push_sender, mock_push_sender)
        self.assertEqual(
            handler._request_context_builder, mock_request_context_builder
        )

    async def test_on_message_send_error_handling(self) -> None:
        """Test error handling in on_message_send when consuming raises ServerError."""
        # Arrange
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)

        # Let task exist
        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task

        # Set up consume_and_break_on_interrupt to raise ServerError
        async def consume_raises_error(*args, **kwargs) -> NoReturn:
            raise ServerError(error=UnsupportedOperationError())

        with patch(
            'a2a.server.tasks.result_aggregator.ResultAggregator.consume_and_break_on_interrupt',
            side_effect=consume_raises_error,
        ):
            # Act
            request = SendMessageRequest(
                id='1',
                params=MessageSendParams(
                    message=Message(
                        **MESSAGE_PAYLOAD,
                        task_id=mock_task.id,
                        context_id=mock_task.context_id,
                    )
                ),
            )

            response = await handler.on_message_send(request)

            # Assert
            self.assertIsInstance(response.root, JSONRPCErrorResponse)
            self.assertEqual(response.root.error, UnsupportedOperationError())  # type: ignore

    async def test_on_message_send_task_id_mismatch(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task
        mock_agent_executor.execute.return_value = None

        async def streaming_coro():
            yield mock_task

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            request = SendMessageRequest(
                id='1',
                params=MessageSendParams(message=Message(**MESSAGE_PAYLOAD)),
            )
            response = await handler.on_message_send(request)
            assert mock_agent_executor.execute.call_count == 1
            self.assertIsInstance(response.root, JSONRPCErrorResponse)
            self.assertIsInstance(response.root.error, InternalError)  # type: ignore

    async def test_on_message_stream_task_id_mismatch(self) -> None:
        mock_agent_executor = AsyncMock(spec=AgentExecutor)
        mock_task_store = AsyncMock(spec=TaskStore)
        request_handler = DefaultRequestHandler(
            mock_agent_executor, mock_task_store
        )

        self.mock_agent_card.capabilities = AgentCapabilities(streaming=True)
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        events: list[Any] = [Task(**MINIMAL_TASK)]

        async def streaming_coro():
            for event in events:
                yield event

        with patch(
            'a2a.server.request_handlers.default_request_handler.EventConsumer.consume_all',
            return_value=streaming_coro(),
        ):
            mock_task_store.get.return_value = None
            mock_agent_executor.execute.return_value = None
            request = SendStreamingMessageRequest(
                id='1',
                params=MessageSendParams(message=Message(**MESSAGE_PAYLOAD)),
            )
            response = handler.on_message_send_stream(request)
            assert isinstance(response, AsyncGenerator)
            collected_events: list[Any] = []
            async for event in response:
                collected_events.append(event)
            assert len(collected_events) == 1
            self.assertIsInstance(
                collected_events[0].root, JSONRPCErrorResponse
            )
            self.assertIsInstance(collected_events[0].root.error, InternalError)

    async def test_on_get_push_notification(self) -> None:
        """Test get_push_notification_config handling"""
        mock_task_store = AsyncMock(spec=TaskStore)

        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task

        # Create request handler without a push notifier
        request_handler = AsyncMock(spec=DefaultRequestHandler)
        task_push_config = TaskPushNotificationConfig(
            task_id=mock_task.id,
            push_notification_config=PushNotificationConfig(
                id='config1', url='http://example.com'
            ),
        )
        request_handler.on_get_task_push_notification_config.return_value = (
            task_push_config
        )

        self.mock_agent_card.capabilities = AgentCapabilities(
            push_notifications=True
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        list_request = GetTaskPushNotificationConfigRequest(
            id='1',
            params=GetTaskPushNotificationConfigParams(
                id=mock_task.id, push_notification_config_id='config1'
            ),
        )
        response = await handler.get_push_notification_config(list_request)
        # Assert
        self.assertIsInstance(
            response.root, GetTaskPushNotificationConfigSuccessResponse
        )
        self.assertEqual(response.root.result, task_push_config)  # type: ignore

    async def test_on_list_push_notification(self) -> None:
        """Test list_push_notification_config handling"""
        mock_task_store = AsyncMock(spec=TaskStore)

        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task

        # Create request handler without a push notifier
        request_handler = AsyncMock(spec=DefaultRequestHandler)
        task_push_config = TaskPushNotificationConfig(
            task_id=mock_task.id,
            push_notification_config=PushNotificationConfig(
                url='http://example.com'
            ),
        )
        request_handler.on_list_task_push_notification_config.return_value = [
            task_push_config
        ]

        self.mock_agent_card.capabilities = AgentCapabilities(
            push_notifications=True
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        list_request = ListTaskPushNotificationConfigRequest(
            id='1', params=ListTaskPushNotificationConfigParams(id=mock_task.id)
        )
        response = await handler.list_push_notification_config(list_request)
        # Assert
        self.assertIsInstance(
            response.root, ListTaskPushNotificationConfigSuccessResponse
        )
        self.assertEqual(response.root.result, [task_push_config])  # type: ignore

    async def test_on_list_push_notification_error(self) -> None:
        """Test list_push_notification_config handling"""
        mock_task_store = AsyncMock(spec=TaskStore)

        mock_task = Task(**MINIMAL_TASK)
        mock_task_store.get.return_value = mock_task

        # Create request handler without a push notifier
        request_handler = AsyncMock(spec=DefaultRequestHandler)
        _ = TaskPushNotificationConfig(
            task_id=mock_task.id,
            push_notification_config=PushNotificationConfig(
                url='http://example.com'
            ),
        )
        # throw server error
        request_handler.on_list_task_push_notification_config.side_effect = (
            ServerError(InternalError())
        )

        self.mock_agent_card.capabilities = AgentCapabilities(
            push_notifications=True
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        list_request = ListTaskPushNotificationConfigRequest(
            id='1', params=ListTaskPushNotificationConfigParams(id=mock_task.id)
        )
        response = await handler.list_push_notification_config(list_request)
        # Assert
        self.assertIsInstance(response.root, JSONRPCErrorResponse)
        self.assertEqual(response.root.error, InternalError())  # type: ignore

    async def test_on_delete_push_notification(self) -> None:
        """Test delete_push_notification_config handling"""

        # Create request handler without a push notifier
        request_handler = AsyncMock(spec=DefaultRequestHandler)
        request_handler.on_delete_task_push_notification_config.return_value = (
            None
        )

        self.mock_agent_card.capabilities = AgentCapabilities(
            push_notifications=True
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        delete_request = DeleteTaskPushNotificationConfigRequest(
            id='1',
            params=DeleteTaskPushNotificationConfigParams(
                id='task1', push_notification_config_id='config1'
            ),
        )
        response = await handler.delete_push_notification_config(delete_request)
        # Assert
        self.assertIsInstance(
            response.root, DeleteTaskPushNotificationConfigSuccessResponse
        )
        self.assertEqual(response.root.result, None)  # type: ignore

    async def test_on_delete_push_notification_error(self) -> None:
        """Test delete_push_notification_config error handling"""

        # Create request handler without a push notifier
        request_handler = AsyncMock(spec=DefaultRequestHandler)
        # throw server error
        request_handler.on_delete_task_push_notification_config.side_effect = (
            ServerError(UnsupportedOperationError())
        )

        self.mock_agent_card.capabilities = AgentCapabilities(
            push_notifications=True
        )
        handler = JSONRPCHandler(self.mock_agent_card, request_handler)
        delete_request = DeleteTaskPushNotificationConfigRequest(
            id='1',
            params=DeleteTaskPushNotificationConfigParams(
                id='task1', push_notification_config_id='config1'
            ),
        )
        response = await handler.delete_push_notification_config(delete_request)
        # Assert
        self.assertIsInstance(response.root, JSONRPCErrorResponse)
        self.assertEqual(response.root.error, UnsupportedOperationError())  # type: ignore

    async def test_get_authenticated_extended_card_success(self) -> None:
        """Test successful retrieval of the authenticated extended agent card."""
        # Arrange
        mock_request_handler = AsyncMock(spec=DefaultRequestHandler)
        mock_extended_card = AgentCard(
            name='Extended Card',
            description='More details',
            url='http://agent.example.com/api',
            version='1.1',
            capabilities=AgentCapabilities(),
            default_input_modes=['text/plain'],
            default_output_modes=['application/json'],
            skills=[],
        )
        handler = JSONRPCHandler(
            self.mock_agent_card,
            mock_request_handler,
            extended_agent_card=mock_extended_card,
            extended_card_modifier=None,
        )
        request = GetAuthenticatedExtendedCardRequest(id='ext-card-req-1')
        call_context = ServerCallContext(state={'foo': 'bar'})

        # Act
        response: GetAuthenticatedExtendedCardResponse = (
            await handler.get_authenticated_extended_card(request, call_context)
        )

        # Assert
        self.assertIsInstance(
            response.root, GetAuthenticatedExtendedCardSuccessResponse
        )
        self.assertEqual(response.root.id, 'ext-card-req-1')
        self.assertEqual(response.root.result, mock_extended_card)

    async def test_get_authenticated_extended_card_not_configured(self) -> None:
        """Test error when authenticated extended agent card is not configured."""
        # Arrange
        mock_request_handler = AsyncMock(spec=DefaultRequestHandler)
        self.mock_agent_card.supports_extended_card = True
        handler = JSONRPCHandler(
            self.mock_agent_card,
            mock_request_handler,
            extended_agent_card=None,
            extended_card_modifier=None,
        )
        request = GetAuthenticatedExtendedCardRequest(id='ext-card-req-2')
        call_context = ServerCallContext(state={'foo': 'bar'})

        # Act
        response: GetAuthenticatedExtendedCardResponse = (
            await handler.get_authenticated_extended_card(request, call_context)
        )

        # Assert
        # Authenticated Extended Card flag is set with no extended card,
        # returns base card in this case.
        self.assertIsInstance(
            response.root, GetAuthenticatedExtendedCardSuccessResponse
        )
        self.assertEqual(response.root.id, 'ext-card-req-2')

    async def test_get_authenticated_extended_card_with_modifier(self) -> None:
        """Test successful retrieval of a dynamically modified extended agent card."""
        # Arrange
        mock_request_handler = AsyncMock(spec=DefaultRequestHandler)
        mock_base_card = AgentCard(
            name='Base Card',
            description='Base details',
            url='http://agent.example.com/api',
            version='1.0',
            capabilities=AgentCapabilities(),
            default_input_modes=['text/plain'],
            default_output_modes=['application/json'],
            skills=[],
        )

        def modifier(card: AgentCard, context: ServerCallContext) -> AgentCard:
            modified_card = card.model_copy(deep=True)
            modified_card.name = 'Modified Card'
            modified_card.description = (
                f'Modified for context: {context.state.get("foo")}'
            )
            return modified_card

        handler = JSONRPCHandler(
            self.mock_agent_card,
            mock_request_handler,
            extended_agent_card=mock_base_card,
            extended_card_modifier=modifier,
        )
        request = GetAuthenticatedExtendedCardRequest(id='ext-card-req-mod')
        call_context = ServerCallContext(state={'foo': 'bar'})

        # Act
        response: GetAuthenticatedExtendedCardResponse = (
            await handler.get_authenticated_extended_card(request, call_context)
        )

        # Assert
        self.assertIsInstance(
            response.root, GetAuthenticatedExtendedCardSuccessResponse
        )
        self.assertEqual(response.root.id, 'ext-card-req-mod')
        modified_card = response.root.result
        self.assertEqual(modified_card.name, 'Modified Card')
        self.assertEqual(modified_card.description, 'Modified for context: bar')
        self.assertEqual(modified_card.version, '1.0')
