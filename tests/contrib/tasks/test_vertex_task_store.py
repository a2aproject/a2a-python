"""
Tests for the VertexTaskStore.

These tests can be run with a real or fake Vertex AI Agent Engine as a backend.
The real ones are skipped by default unless the necessary environment variables\
are set, which prevents them from failing in GitHub Actions.

To run these tests locally, you can use the provided script:
    ./run_vertex_tests.sh

The following environment variables are required for the real backend:
    VERTEX_PROJECT="your-project" \
    VERTEX_LOCATION="your-location" \
    VERTEX_BASE_URL="your-base-url" \
    VERTEX_API_VERSION="your-api-version" \
"""

import os

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from .fake_vertex_client import FakeVertexClient


# Skip the entire test module if vertexai is not installed
pytest.importorskip(
    'vertexai', reason='Vertex Task Store tests require vertexai'
)
import vertexai


# Skip the real backend tests if required environment variables are not set
missing_env_vars = not all(
    os.environ.get(var)
    for var in [
        'VERTEX_PROJECT',
        'VERTEX_LOCATION',
        'VERTEX_BASE_URL',
        'VERTEX_API_VERSION',
    ]
)


@pytest.fixture(
    scope='module',
    params=[
        'fake',
        pytest.param(
            'real',
            marks=pytest.mark.skipif(
                missing_env_vars,
                reason='Missing required environment variables for real Vertex Task Store.',
            ),
        ),
    ],
)
def backend_type(request) -> str:
    return request.param


from a2a.contrib.tasks.vertex_task_store import VertexTaskStore
from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
)


# Minimal Task object for testing
MINIMAL_TASK_OBJ = Task(
    id='task-abc',
    context_id='session-xyz',
    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
)
MINIMAL_TASK_OBJ.metadata['test_key'] = 'test_value'


from collections.abc import Generator


@pytest.fixture(scope='module')
def agent_engine_resource_id(backend_type: str) -> Generator[str, None, None]:
    """
    Module-scoped fixture that creates and deletes a single Agent Engine
    for all the tests. For fake backend, it yields a mock resource.
    """
    if backend_type == 'fake':
        yield 'projects/mock-project/locations/mock-location/agentEngines/mock-engine'
        return

    project = os.environ.get('VERTEX_PROJECT')
    location = os.environ.get('VERTEX_LOCATION')
    base_url = os.environ.get('VERTEX_BASE_URL')

    client = vertexai.Client(project=project, location=location)
    client._api_client._http_options.base_url = base_url

    agent_engine = client.agent_engines.create()
    yield agent_engine.api_resource.name
    agent_engine.delete()


@pytest_asyncio.fixture
async def vertex_store(
    backend_type: str,
    agent_engine_resource_id: str,
) -> AsyncGenerator[VertexTaskStore, None]:
    """
    Function-scoped fixture providing a fresh VertexTaskStore per test,
    reusing the module-scoped engine. Uses fake client for 'fake' backend.
    """
    if backend_type == 'fake':
        client = FakeVertexClient()
    else:
        project = os.environ.get('VERTEX_PROJECT')
        location = os.environ.get('VERTEX_LOCATION')
        base_url = os.environ.get('VERTEX_BASE_URL')
        api_version = os.environ.get('VERTEX_API_VERSION')

        client = vertexai.Client(project=project, location=location)
        client._api_client._http_options.base_url = base_url
        client._api_client._http_options.api_version = api_version

    store = VertexTaskStore(
        client=client,  # type: ignore
        agent_engine_resource_id=agent_engine_resource_id,
    )
    yield store


@pytest.mark.asyncio
async def test_save_task(vertex_store: VertexTaskStore) -> None:
    """Test saving a task to the VertexTaskStore."""
    # Ensure unique ID for parameterized tests if needed, or rely on table isolation
    task_to_save = Task()
    task_to_save.CopyFrom(MINIMAL_TASK_OBJ)
    task_to_save.id = 'save-test-task-2'
    await vertex_store.save(task_to_save, ServerCallContext())

    retrieved_task = await vertex_store.get(
        task_to_save.id, ServerCallContext()
    )
    assert retrieved_task is not None
    assert retrieved_task.id == task_to_save.id

    assert retrieved_task == task_to_save


@pytest.mark.asyncio
async def test_get_task(vertex_store: VertexTaskStore) -> None:
    """Test retrieving a task from the VertexTaskStore."""
    task_id = 'get-test-task-1'
    task_to_save = Task()
    task_to_save.CopyFrom(MINIMAL_TASK_OBJ)
    task_to_save.id = task_id
    await vertex_store.save(task_to_save, ServerCallContext())

    retrieved_task = await vertex_store.get(
        task_to_save.id, ServerCallContext()
    )
    assert retrieved_task is not None
    assert retrieved_task.id == task_to_save.id
    assert retrieved_task.context_id == task_to_save.context_id
    assert retrieved_task.status.state == TaskState.TASK_STATE_SUBMITTED


@pytest.mark.asyncio
async def test_get_nonexistent_task(
    vertex_store: VertexTaskStore,
) -> None:
    """Test retrieving a nonexistent task."""
    retrieved_task = await vertex_store.get(
        'nonexistent-task-id', ServerCallContext()
    )
    assert retrieved_task is None


@pytest.mark.asyncio
async def test_save_and_get_detailed_task(
    vertex_store: VertexTaskStore,
) -> None:
    """Test saving and retrieving a task with more fields populated."""
    task_id = 'detailed-task-test-vertex'
    test_task = Task(
        id=task_id,
        context_id='test-session-1',
        status=TaskStatus(
            state=TaskState.TASK_STATE_SUBMITTED,
        ),
        artifacts=[
            Artifact(
                artifact_id='artifact-1',
                parts=[Part(text='hello')],
            )
        ],
    )
    test_task.metadata['key1'] = 'value1'
    test_task.metadata['key2'] = 123

    await vertex_store.save(test_task, ServerCallContext())
    retrieved_task = await vertex_store.get(test_task.id, ServerCallContext())

    assert retrieved_task is not None
    assert retrieved_task.id == test_task.id
    assert retrieved_task.context_id == test_task.context_id
    assert retrieved_task.status.state == TaskState.TASK_STATE_SUBMITTED
    assert retrieved_task.metadata['key1'] == 'value1'
    assert retrieved_task.metadata['key2'] == 123
    assert retrieved_task.artifacts == test_task.artifacts


@pytest.mark.asyncio
async def test_update_task_status_and_metadata(
    vertex_store: VertexTaskStore,
) -> None:
    """Test updating an existing task."""
    task_id = 'update-test-task-1'
    original_task = Task(
        id=task_id,
        context_id='session-update',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        artifacts=[],
        history=[],
    )
    await vertex_store.save(original_task, ServerCallContext())

    retrieved_before_update = await vertex_store.get(
        task_id, ServerCallContext()
    )
    assert retrieved_before_update is not None
    assert (
        retrieved_before_update.status.state == TaskState.TASK_STATE_SUBMITTED
    )
    assert retrieved_before_update.metadata == {}

    updated_task = Task()
    updated_task.CopyFrom(original_task)
    updated_task.status.state = TaskState.TASK_STATE_COMPLETED
    updated_task.status.timestamp.FromJsonString('2023-01-02T11:00:00Z')
    updated_task.metadata.update({'update_key': 'update_value'})

    await vertex_store.save(updated_task, ServerCallContext())

    retrieved_after_update = await vertex_store.get(
        task_id, ServerCallContext()
    )
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.TASK_STATE_COMPLETED
    assert retrieved_after_update.metadata == {'update_key': 'update_value'}


@pytest.mark.asyncio
async def test_update_task_add_artifact(vertex_store: VertexTaskStore) -> None:
    """Test updating an existing task by adding an artifact."""
    task_id = 'update-test-task-2'
    original_task = Task(
        id=task_id,
        context_id='session-update',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        artifacts=[
            Artifact(
                artifact_id='artifact-1',
                parts=[Part(text='hello')],
            )
        ],
        history=[],
    )
    await vertex_store.save(original_task, ServerCallContext())

    retrieved_before_update = await vertex_store.get(
        task_id, ServerCallContext()
    )
    assert retrieved_before_update is not None
    assert (
        retrieved_before_update.status.state == TaskState.TASK_STATE_SUBMITTED
    )
    assert retrieved_before_update.metadata == {}

    updated_task = Task()
    updated_task.CopyFrom(original_task)
    updated_task.status.state = TaskState.TASK_STATE_WORKING
    updated_task.status.timestamp.FromJsonString('2023-01-02T11:00:00Z')

    updated_task.artifacts.append(
        Artifact(
            artifact_id='artifact-2',
            parts=[Part(text='world')],
        )
    )

    await vertex_store.save(updated_task, ServerCallContext())

    retrieved_after_update = await vertex_store.get(
        task_id, ServerCallContext()
    )
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.TASK_STATE_WORKING

    assert retrieved_after_update.artifacts == [
        Artifact(
            artifact_id='artifact-1',
            parts=[Part(text='hello')],
        ),
        Artifact(
            artifact_id='artifact-2',
            parts=[Part(text='world')],
        ),
    ]


@pytest.mark.asyncio
async def test_update_task_update_artifact(
    vertex_store: VertexTaskStore,
) -> None:
    """Test updating an existing task by changing an artifact."""
    task_id = 'update-test-task-3'
    original_task = Task(
        id=task_id,
        context_id='session-update',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        artifacts=[
            Artifact(
                artifact_id='artifact-1',
                parts=[Part(text='hello')],
            ),
            Artifact(
                artifact_id='artifact-2',
                parts=[Part(text='world')],
            ),
        ],
        history=[],
    )
    await vertex_store.save(original_task, ServerCallContext())

    retrieved_before_update = await vertex_store.get(
        task_id, ServerCallContext()
    )
    assert retrieved_before_update is not None
    assert (
        retrieved_before_update.status.state == TaskState.TASK_STATE_SUBMITTED
    )
    assert retrieved_before_update.metadata == {}

    updated_task = Task()
    updated_task.CopyFrom(original_task)
    updated_task.status.state = TaskState.TASK_STATE_WORKING
    updated_task.status.timestamp.FromJsonString('2023-01-02T11:00:00Z')

    updated_task.artifacts[0].parts[0].text = 'ahoy'

    await vertex_store.save(updated_task, ServerCallContext())

    retrieved_after_update = await vertex_store.get(
        task_id, ServerCallContext()
    )
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.TASK_STATE_WORKING

    assert retrieved_after_update.artifacts == [
        Artifact(
            artifact_id='artifact-1',
            parts=[Part(text='ahoy')],
        ),
        Artifact(
            artifact_id='artifact-2',
            parts=[Part(text='world')],
        ),
    ]


@pytest.mark.asyncio
async def test_update_task_delete_artifact(
    vertex_store: VertexTaskStore,
) -> None:
    """Test updating an existing task by deleting an artifact."""
    task_id = 'update-test-task-4'
    original_task = Task(
        id=task_id,
        context_id='session-update',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        artifacts=[
            Artifact(
                artifact_id='artifact-1',
                parts=[Part(text='hello')],
            ),
            Artifact(
                artifact_id='artifact-2',
                parts=[Part(text='world')],
            ),
        ],
        history=[],
    )
    await vertex_store.save(original_task, ServerCallContext())

    retrieved_before_update = await vertex_store.get(
        task_id, ServerCallContext()
    )
    assert retrieved_before_update is not None
    assert (
        retrieved_before_update.status.state == TaskState.TASK_STATE_SUBMITTED
    )
    assert retrieved_before_update.metadata == {}

    updated_task = Task()
    updated_task.CopyFrom(original_task)
    updated_task.status.state = TaskState.TASK_STATE_WORKING
    updated_task.status.timestamp.FromJsonString('2023-01-02T11:00:00Z')

    del updated_task.artifacts[1]

    await vertex_store.save(updated_task, ServerCallContext())

    retrieved_after_update = await vertex_store.get(
        task_id, ServerCallContext()
    )
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.TASK_STATE_WORKING

    assert retrieved_after_update.artifacts == [
        Artifact(
            artifact_id='artifact-1',
            parts=[Part(text='hello')],
        )
    ]


@pytest.mark.asyncio
async def test_metadata_field_mapping(
    vertex_store: VertexTaskStore,
) -> None:
    """Test that metadata field is correctly mapped between the core types and vertex.

    This test verifies:
    1. Metadata can be None
    2. Metadata can be a simple dict
    3. Metadata can contain nested structures
    4. Metadata is correctly saved and retrieved
    5. The mapping between task.metadata and task_metadata column works
    """
    # Test 1: Task with no metadata (None)
    task_no_metadata = Task(
        id='task-metadata-test-1',
        context_id='session-meta-1',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )
    await vertex_store.save(task_no_metadata, ServerCallContext())
    retrieved_no_metadata = await vertex_store.get(
        'task-metadata-test-1', ServerCallContext()
    )
    assert retrieved_no_metadata is not None
    assert retrieved_no_metadata.metadata == {}

    # Test 2: Task with simple metadata
    simple_metadata = {'key': 'value', 'number': 42, 'boolean': True}
    task_simple_metadata = Task(
        id='task-metadata-test-2',
        context_id='session-meta-2',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        metadata=simple_metadata,
    )
    await vertex_store.save(task_simple_metadata, ServerCallContext())
    retrieved_simple = await vertex_store.get(
        'task-metadata-test-2', ServerCallContext()
    )
    assert retrieved_simple is not None
    assert retrieved_simple.metadata == simple_metadata

    # Test 3: Task with complex nested metadata
    complex_metadata = {
        'level1': {
            'level2': {
                'level3': ['a', 'b', 'c'],
                'numeric': 3.14159,
            },
            'array': [1, 2, {'nested': 'value'}],
        },
        'special_chars': 'Hello\nWorld\t!',
        'unicode': '🚀 Unicode test 你好',
        'null_value': None,
    }
    task_complex_metadata = Task(
        id='task-metadata-test-3',
        context_id='session-meta-3',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        metadata=complex_metadata,
    )
    await vertex_store.save(task_complex_metadata, ServerCallContext())
    retrieved_complex = await vertex_store.get(
        'task-metadata-test-3', ServerCallContext()
    )
    assert retrieved_complex is not None
    assert retrieved_complex.metadata == complex_metadata

    # Test 4: Update metadata from None to dict
    task_update_metadata = Task(
        id='task-metadata-test-4',
        context_id='session-meta-4',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )
    await vertex_store.save(task_update_metadata, ServerCallContext())

    # Update metadata
    task_update_metadata.metadata.Clear()
    task_update_metadata.metadata.update(
        {'updated': True, 'timestamp': '2024-01-01'}
    )
    await vertex_store.save(task_update_metadata, ServerCallContext())

    retrieved_updated = await vertex_store.get(
        'task-metadata-test-4', ServerCallContext()
    )
    assert retrieved_updated is not None
    assert retrieved_updated.metadata == {
        'updated': True,
        'timestamp': '2024-01-01',
    }

    # Test 5: Update metadata from dict to None
    task_update_metadata.metadata.Clear()
    await vertex_store.save(task_update_metadata, ServerCallContext())

    retrieved_none = await vertex_store.get(
        'task-metadata-test-4', ServerCallContext()
    )
    assert retrieved_none is not None
    assert retrieved_none.metadata == {}


@pytest.mark.asyncio
async def test_update_task_status_details(
    vertex_store: VertexTaskStore,
) -> None:
    """Test updating an existing task by changing the status details (message) with part metadata."""
    task_id = 'update-test-task-status-details'
    original_task = Task(
        id=task_id,
        context_id='session-update',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        metadata=None,
        artifacts=[],
        history=[],
    )
    await vertex_store.save(original_task, ServerCallContext())

    retrieved_before_update = await vertex_store.get(
        task_id, ServerCallContext()
    )
    assert retrieved_before_update is not None
    assert (
        retrieved_before_update.status.state == TaskState.TASK_STATE_SUBMITTED
    )

    updated_task = Task()
    updated_task.CopyFrom(original_task)
    updated_task.status.state = TaskState.TASK_STATE_FAILED
    updated_task.status.timestamp.FromJsonString('2023-01-02T11:00:00Z')
    updated_task.status.message.CopyFrom(
        Message(
            message_id='msg-error-1',
            role=Role.ROLE_AGENT,
            parts=[
                Part(
                    text='Task failed due to an unknown error',
                    metadata={'error_code': 'UNKNOWN', 'retryable': False},
                )
            ],
        )
    )

    await vertex_store.save(updated_task, ServerCallContext())

    retrieved_after_update = await vertex_store.get(
        task_id, ServerCallContext()
    )
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.TASK_STATE_FAILED
    assert retrieved_after_update.status.message is not None
    assert retrieved_after_update.status.message.message_id == 'msg-error-1'
    assert retrieved_after_update.status.message.role == Role.ROLE_AGENT
    assert len(retrieved_after_update.status.message.parts) == 1

    part = retrieved_after_update.status.message.parts[0]
    assert part.text == 'Task failed due to an unknown error'
    assert part.metadata == {'error_code': 'UNKNOWN', 'retryable': False}

    # Also test clearing the message
    cleared_task = Task()
    cleared_task.CopyFrom(updated_task)
    cleared_task.status.ClearField('message')

    await vertex_store.save(cleared_task, ServerCallContext())
    retrieved_cleared = await vertex_store.get(task_id, ServerCallContext())
    assert retrieved_cleared is not None
    assert not retrieved_cleared.status.HasField('message')
