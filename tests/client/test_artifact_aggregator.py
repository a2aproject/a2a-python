from unittest.mock import AsyncMock

import pytest

from a2a.client import BaseClient, ClientConfig
from a2a.client.artifact_aggregator import ArtifactsAggregator
from a2a.client.transports import ClientTransport
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Artifact,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
)


@pytest.fixture
def mock_transport() -> AsyncMock:
    return AsyncMock(spec=ClientTransport)


@pytest.fixture
def sample_agent_card() -> AgentCard:
    return AgentCard(
        name='Test Agent',
        description='An agent for testing',
        supported_interfaces=[
            AgentInterface(url='http://test.com', protocol_binding='HTTP+JSON')
        ],
        version='1.0',
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[],
    )


@pytest.fixture
def sample_message() -> Message:
    return Message(
        role=Role.ROLE_USER,
        message_id='msg-1',
        parts=[Part(text='Hello')],
    )


@pytest.fixture
def base_client(
    sample_agent_card: AgentCard, mock_transport: AsyncMock
) -> BaseClient:
    config = ClientConfig(streaming=True)
    return BaseClient(
        card=sample_agent_card,
        config=config,
        transport=mock_transport,
        interceptors=[],
    )


class TestArtifactsAggregator:
    @pytest.mark.asyncio
    async def test_get_artifact_assembles_multiple_chunks(
        self,
        base_client: BaseClient,
        mock_transport: AsyncMock,
        sample_message: Message,
    ) -> None:
        """Test that get_artifact correctly assembles an artifact from multiple chunks."""

        async def create_stream(*args, **kwargs):
            artifact_update_other = TaskArtifactUpdateEvent(
                task_id='task-123',
                context_id='ctx-456',
                artifact=Artifact(
                    artifact_id='artifact-other',
                    name='other',
                    parts=[Part(text='ignored')],
                ),
                append=False,
                last_chunk=True,
            )
            stream_response_other = StreamResponse()
            stream_response_other.artifact_update.CopyFrom(
                artifact_update_other
            )
            yield stream_response_other

            artifact_update_chunk1 = TaskArtifactUpdateEvent(
                task_id='task-123',
                context_id='ctx-456',
                artifact=Artifact(
                    artifact_id='artifact-1',
                    name='result',
                    parts=[Part(text='Hello ')],
                ),
                append=False,
                last_chunk=False,
            )

            artifact_update_chunk2 = TaskArtifactUpdateEvent(
                task_id='task-123',
                context_id='ctx-456',
                artifact=Artifact(
                    artifact_id='artifact-1',
                    name='result',
                    parts=[Part(text='World')],
                ),
                append=True,
                last_chunk=True,
            )

            stream_response_1 = StreamResponse()
            stream_response_1.artifact_update.CopyFrom(artifact_update_chunk1)

            stream_response_2 = StreamResponse()
            stream_response_2.artifact_update.CopyFrom(artifact_update_chunk2)

            yield stream_response_1
            yield stream_response_2

        mock_transport.send_message_streaming.return_value = create_stream()

        request = SendMessageRequest(message=sample_message)

        artifact_aggregator = ArtifactsAggregator.from_stream(
            base_client.send_message(request)
        )
        artifact = await artifact_aggregator.get_artifact('artifact-1')

        assert artifact is not None
        assert artifact.artifact_id == 'artifact-1'
        assert artifact.parts[0].text == 'Hello '
        assert artifact.parts[1].text == 'World'

    @pytest.mark.asyncio
    async def test_get_artifact_single_chunk(
        self,
        base_client: BaseClient,
        mock_transport: AsyncMock,
        sample_message: Message,
    ) -> None:
        """Test that get_artifact correctly returns an artifact delivered in a single chunk."""

        async def create_stream(*args, **kwargs):
            artifact_update = TaskArtifactUpdateEvent(
                task_id='task-123',
                context_id='ctx-456',
                artifact=Artifact(
                    artifact_id='artifact-1',
                    name='result',
                    parts=[Part(text='Hello World')],
                ),
                append=False,
                last_chunk=True,
            )

            stream_response = StreamResponse()
            stream_response.artifact_update.CopyFrom(artifact_update)
            yield stream_response

        mock_transport.send_message_streaming.return_value = create_stream()

        request = SendMessageRequest(message=sample_message)

        artifact_aggregator = ArtifactsAggregator.from_stream(
            base_client.send_message(request)
        )
        artifact = await artifact_aggregator.get_artifact('artifact-1')

        assert artifact is not None
        assert artifact.artifact_id == 'artifact-1'
        assert artifact.parts[0].text == 'Hello World'

    @pytest.mark.asyncio
    async def test_get_all_artifacts_assembles_multiple_artifacts(
        self,
        base_client: BaseClient,
        mock_transport: AsyncMock,
        sample_message: Message,
    ) -> None:
        """Test that get_all_artifacts correctly assembles multiple artifacts from the stream."""

        async def create_stream(*args, **kwargs):
            artifact_update_1 = TaskArtifactUpdateEvent(
                task_id='task-123',
                context_id='ctx-456',
                artifact=Artifact(
                    artifact_id='artifact-1',
                    name='result-1',
                    parts=[Part(text='Hello ')],
                ),
                append=False,
                last_chunk=False,
            )
            artifact_update_2 = TaskArtifactUpdateEvent(
                task_id='task-123',
                context_id='ctx-456',
                artifact=Artifact(
                    artifact_id='artifact-2',
                    name='result-2',
                    parts=[Part(text='World')],
                ),
                append=False,
                last_chunk=True,
            )
            artifact_update_3 = TaskArtifactUpdateEvent(
                task_id='task-123',
                context_id='ctx-456',
                artifact=Artifact(
                    artifact_id='artifact-1',
                    name='result-1',
                    parts=[Part(text='World')],
                ),
                append=True,
                last_chunk=True,
            )

            for update in [
                artifact_update_1,
                artifact_update_2,
                artifact_update_3,
            ]:
                sr = StreamResponse()
                sr.artifact_update.CopyFrom(update)
                yield sr

        mock_transport.send_message_streaming.return_value = create_stream()

        request = SendMessageRequest(message=sample_message)
        aggregator = ArtifactsAggregator.from_stream(
            base_client.send_message(request)
        )
        artifacts = await aggregator.get_all_artifacts()

        assert len(artifacts) == 2
        artifact_map = {a.artifact_id: a for a in artifacts}

        assert artifact_map['artifact-1'].parts[0].text == 'Hello '
        assert artifact_map['artifact-1'].parts[1].text == 'World'
        assert artifact_map['artifact-2'].parts[0].text == 'World'

    @pytest.mark.asyncio
    async def test_get_all_artifacts_skips_non_artifact_events(
        self,
        base_client: BaseClient,
        mock_transport: AsyncMock,
        sample_message: Message,
    ) -> None:
        """Test that get_all_artifacts ignores non-artifact events in the stream."""

        async def create_stream(*args, **kwargs):
            task = Task(
                id='task-123',
                context_id='ctx-456',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
            sr_task = StreamResponse()
            sr_task.task.CopyFrom(task)
            yield sr_task

            artifact_update = TaskArtifactUpdateEvent(
                task_id='task-123',
                context_id='ctx-456',
                artifact=Artifact(
                    artifact_id='artifact-1',
                    name='result',
                    parts=[Part(text='Hello')],
                ),
                append=False,
                last_chunk=True,
            )
            sr_artifact = StreamResponse()
            sr_artifact.artifact_update.CopyFrom(artifact_update)
            yield sr_artifact

        mock_transport.send_message_streaming.return_value = create_stream()

        request = SendMessageRequest(message=sample_message)
        aggregator = ArtifactsAggregator.from_stream(
            base_client.send_message(request)
        )
        artifacts = await aggregator.get_all_artifacts()

        assert len(artifacts) == 1
        assert artifacts[0].artifact_id == 'artifact-1'
        assert artifacts[0].parts[0].text == 'Hello'

    @pytest.mark.asyncio
    async def test_get_artifact_skips_non_artifact_events(
        self,
        base_client: BaseClient,
        mock_transport: AsyncMock,
        sample_message: Message,
    ) -> None:
        """Test that get_artifact ignores non-artifact events in the stream."""

        async def create_stream(*args, **kwargs):
            task = Task(
                id='task-123',
                context_id='ctx-456',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
            sr_task = StreamResponse()
            sr_task.task.CopyFrom(task)
            yield sr_task

            artifact_update = TaskArtifactUpdateEvent(
                task_id='task-123',
                context_id='ctx-456',
                artifact=Artifact(
                    artifact_id='artifact-1',
                    name='result',
                    parts=[Part(text='Hello')],
                ),
                append=False,
                last_chunk=True,
            )
            sr_artifact = StreamResponse()
            sr_artifact.artifact_update.CopyFrom(artifact_update)
            yield sr_artifact

        mock_transport.send_message_streaming.return_value = create_stream()

        request = SendMessageRequest(message=sample_message)
        aggregator = ArtifactsAggregator.from_stream(
            base_client.send_message(request)
        )
        artifact = await aggregator.get_artifact('artifact-1')

        assert artifact is not None
        assert artifact.artifact_id == 'artifact-1'
        assert artifact.parts[0].text == 'Hello'

    @pytest.mark.asyncio
    async def test_get_artifact_returns_none_when_not_found(
        self,
        base_client: BaseClient,
        mock_transport: AsyncMock,
        sample_message: Message,
    ) -> None:
        """Test that get_artifact returns None when the artifact_id is not found in the stream."""

        async def create_stream(*args, **kwargs):
            artifact_update = TaskArtifactUpdateEvent(
                task_id='task-123',
                context_id='ctx-456',
                artifact=Artifact(
                    artifact_id='artifact-other',
                    name='result',
                    parts=[Part(text='Hello')],
                ),
                append=False,
                last_chunk=True,
            )
            stream_response = StreamResponse()
            stream_response.artifact_update.CopyFrom(artifact_update)
            yield stream_response

        mock_transport.send_message_streaming.return_value = create_stream()

        request = SendMessageRequest(message=sample_message)
        aggregator = ArtifactsAggregator.from_stream(
            base_client.send_message(request)
        )
        artifact = await aggregator.get_artifact('artifact-1')

        assert artifact is None

    @pytest.mark.asyncio
    async def test_get_all_artifacts_resets_artifact_on_append_false(
        self,
        base_client: BaseClient,
        mock_transport: AsyncMock,
        sample_message: Message,
    ) -> None:
        """Test that get_all_artifacts resets an artifact's parts when append=False is received."""

        async def create_stream(*args, **kwargs):
            for update in [
                TaskArtifactUpdateEvent(
                    task_id='task-123',
                    context_id='ctx-456',
                    artifact=Artifact(
                        artifact_id='artifact-1',
                        name='v1',
                        parts=[Part(text='old')],
                    ),
                    append=False,
                    last_chunk=False,
                ),
                TaskArtifactUpdateEvent(
                    task_id='task-123',
                    context_id='ctx-456',
                    artifact=Artifact(
                        artifact_id='artifact-1',
                        name='v2',
                        parts=[Part(text='new')],
                    ),
                    append=False,
                    last_chunk=True,
                ),
            ]:
                sr = StreamResponse()
                sr.artifact_update.CopyFrom(update)
                yield sr

        mock_transport.send_message_streaming.return_value = create_stream()

        aggregator = ArtifactsAggregator.from_stream(
            base_client.send_message(SendMessageRequest(message=sample_message))
        )
        artifacts = await aggregator.get_all_artifacts()

        assert len(artifacts) == 1
        assert artifacts[0].parts[0].text == 'new'
        assert artifacts[0].name == 'v2'

    @pytest.mark.asyncio
    async def test_get_artifact_resets_on_append_false(
        self,
        base_client: BaseClient,
        mock_transport: AsyncMock,
        sample_message: Message,
    ) -> None:
        """Test that get_artifact resets parts when append=False is received mid-stream."""

        async def create_stream(*args, **kwargs):
            for update in [
                TaskArtifactUpdateEvent(
                    task_id='task-123',
                    context_id='ctx-456',
                    artifact=Artifact(
                        artifact_id='artifact-1',
                        name='v1',
                        parts=[Part(text='old')],
                    ),
                    append=False,
                    last_chunk=False,
                ),
                TaskArtifactUpdateEvent(
                    task_id='task-123',
                    context_id='ctx-456',
                    artifact=Artifact(
                        artifact_id='artifact-1',
                        name='v2',
                        parts=[Part(text='new')],
                    ),
                    append=False,
                    last_chunk=True,
                ),
            ]:
                sr = StreamResponse()
                sr.artifact_update.CopyFrom(update)
                yield sr

        mock_transport.send_message_streaming.return_value = create_stream()

        aggregator = ArtifactsAggregator.from_stream(
            base_client.send_message(SendMessageRequest(message=sample_message))
        )
        artifact = await aggregator.get_artifact('artifact-1')

        assert artifact is not None
        assert len(artifact.parts) == 1
        assert artifact.parts[0].text == 'new'
        assert artifact.name == 'v2'
