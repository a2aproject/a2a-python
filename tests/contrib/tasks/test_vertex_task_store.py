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
import sys


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
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


# Minimal Task object for testing - remains the same
task_status_submitted = TaskStatus(state=TaskState.submitted)
MINIMAL_TASK_OBJ = Task(
    id='task-abc',
    context_id='session-xyz',
    status=task_status_submitted,
    kind='task',
    metadata={'test_key': 'test_value'},
    artifacts=[],
    history=[],
)


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
        sys.path.append(os.path.dirname(__file__))
        from fake_vertex_client import FakeVertexClient

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
    task_to_save = MINIMAL_TASK_OBJ.model_copy(deep=True)
    # Ensure unique ID for parameterized tests if needed, or rely on table isolation
    task_to_save.id = 'save-test-task-2'
    await vertex_store.save(task_to_save)

    retrieved_task = await vertex_store.get(task_to_save.id)
    assert retrieved_task is not None
    assert retrieved_task.id == task_to_save.id

    assert retrieved_task.model_dump() == task_to_save.model_dump()


@pytest.mark.asyncio
async def test_get_task(vertex_store: VertexTaskStore) -> None:
    """Test retrieving a task from the VertexTaskStore."""
    task_id = 'get-test-task-1'
    task_to_save = MINIMAL_TASK_OBJ.model_copy(update={'id': task_id})
    await vertex_store.save(task_to_save)

    retrieved_task = await vertex_store.get(task_to_save.id)
    assert retrieved_task is not None
    assert retrieved_task.id == task_to_save.id
    assert retrieved_task.context_id == task_to_save.context_id
    assert retrieved_task.status.state == TaskState.submitted


@pytest.mark.asyncio
async def test_get_nonexistent_task(
    vertex_store: VertexTaskStore,
) -> None:
    """Test retrieving a nonexistent task."""
    retrieved_task = await vertex_store.get('nonexistent-task-id')
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
            state=TaskState.submitted,
        ),
        kind='task',
        metadata={'key1': 'value1', 'key2': 123},
        artifacts=[
            Artifact(
                artifact_id='artifact-1',
                parts=[Part(root=TextPart(text='hello'))],
            )
        ],
    )

    await vertex_store.save(test_task)
    retrieved_task = await vertex_store.get(test_task.id)

    assert retrieved_task is not None
    assert retrieved_task.id == test_task.id
    assert retrieved_task.context_id == test_task.context_id
    assert retrieved_task.status.state == TaskState.submitted
    assert retrieved_task.metadata == {'key1': 'value1', 'key2': 123}

    # Pydantic models handle their own serialization for comparison if model_dump is used
    assert (
        retrieved_task.model_dump()['artifacts']
        == test_task.model_dump()['artifacts']
    )


@pytest.mark.asyncio
async def test_update_task_status_and_metadata(
    vertex_store: VertexTaskStore,
) -> None:
    """Test updating an existing task."""
    task_id = 'update-test-task-1'
    original_task = Task(
        id=task_id,
        context_id='session-update',
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=None,
        artifacts=[],
        history=[],
    )
    await vertex_store.save(original_task)

    retrieved_before_update = await vertex_store.get(task_id)
    assert retrieved_before_update is not None
    assert retrieved_before_update.status.state == TaskState.submitted
    assert retrieved_before_update.metadata == {}

    updated_task = original_task.model_copy(deep=True)
    updated_task.status.state = TaskState.completed
    updated_task.status.timestamp = '2023-01-02T11:00:00Z'
    updated_task.metadata = {'update_key': 'update_value'}

    await vertex_store.save(updated_task)

    retrieved_after_update = await vertex_store.get(task_id)
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.completed
    assert retrieved_after_update.metadata == {'update_key': 'update_value'}


@pytest.mark.asyncio
async def test_update_task_add_artifact(vertex_store: VertexTaskStore) -> None:
    """Test updating an existing task by adding an artifact."""
    task_id = 'update-test-task-2'
    original_task = Task(
        id=task_id,
        context_id='session-update',
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=None,
        artifacts=[
            Artifact(
                artifact_id='artifact-1',
                parts=[Part(root=TextPart(text='hello'))],
            )
        ],
        history=[],
    )
    await vertex_store.save(original_task)

    retrieved_before_update = await vertex_store.get(task_id)
    assert retrieved_before_update is not None
    assert retrieved_before_update.status.state == TaskState.submitted
    assert retrieved_before_update.metadata == {}

    updated_task = original_task.model_copy(deep=True)
    updated_task.status.state = TaskState.working
    updated_task.status.timestamp = '2023-01-02T11:00:00Z'

    updated_task.artifacts.append(
        Artifact(
            artifact_id='artifact-2',
            parts=[Part(root=TextPart(text='world'))],
        )
    )

    await vertex_store.save(updated_task)

    retrieved_after_update = await vertex_store.get(task_id)
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.working

    assert retrieved_after_update.artifacts == [
        Artifact(
            artifact_id='artifact-1',
            parts=[Part(root=TextPart(text='hello'))],
        ),
        Artifact(
            artifact_id='artifact-2',
            parts=[Part(root=TextPart(text='world'))],
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
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=None,  # Explicitly None
        artifacts=[
            Artifact(
                artifact_id='artifact-1',
                parts=[Part(root=TextPart(text='hello'))],
            ),
            Artifact(
                artifact_id='artifact-2',
                parts=[Part(root=TextPart(text='world'))],
            ),
        ],
        history=[],
    )
    await vertex_store.save(original_task)

    retrieved_before_update = await vertex_store.get(task_id)
    assert retrieved_before_update is not None
    assert retrieved_before_update.status.state == TaskState.submitted
    assert retrieved_before_update.metadata == {}

    updated_task = original_task.model_copy(deep=True)
    updated_task.status.state = TaskState.working
    updated_task.status.timestamp = '2023-01-02T11:00:00Z'

    updated_task.artifacts[0].parts[0].root.text = 'ahoy'

    await vertex_store.save(updated_task)

    retrieved_after_update = await vertex_store.get(task_id)
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.working

    assert retrieved_after_update.artifacts == [
        Artifact(
            artifact_id='artifact-1',
            parts=[Part(root=TextPart(text='ahoy'))],
        ),
        Artifact(
            artifact_id='artifact-2',
            parts=[Part(root=TextPart(text='world'))],
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
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=None,
        artifacts=[
            Artifact(
                artifact_id='artifact-1',
                parts=[Part(root=TextPart(text='hello'))],
            ),
            Artifact(
                artifact_id='artifact-2',
                parts=[Part(root=TextPart(text='world'))],
            ),
        ],
        history=[],
    )
    await vertex_store.save(original_task)

    retrieved_before_update = await vertex_store.get(task_id)
    assert retrieved_before_update is not None
    assert retrieved_before_update.status.state == TaskState.submitted
    assert retrieved_before_update.metadata == {}

    updated_task = original_task.model_copy(deep=True)
    updated_task.status.state = TaskState.working
    updated_task.status.timestamp = '2023-01-02T11:00:00Z'

    del updated_task.artifacts[1]

    await vertex_store.save(updated_task)

    retrieved_after_update = await vertex_store.get(task_id)
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.working

    assert retrieved_after_update.artifacts == [
        Artifact(
            artifact_id='artifact-1',
            parts=[Part(root=TextPart(text='hello'))],
        )
    ]


@pytest.mark.asyncio
async def test_metadata_field_mapping(
    vertex_store: VertexTaskStore,
) -> None:
    """Test that metadata field is correctly mapped between Pydantic and SQLAlchemy.

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
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=None,
    )
    await vertex_store.save(task_no_metadata)
    retrieved_no_metadata = await vertex_store.get('task-metadata-test-1')
    assert retrieved_no_metadata is not None
    assert retrieved_no_metadata.metadata == {}

    # Test 2: Task with simple metadata
    simple_metadata = {'key': 'value', 'number': 42, 'boolean': True}
    task_simple_metadata = Task(
        id='task-metadata-test-2',
        context_id='session-meta-2',
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=simple_metadata,
    )
    await vertex_store.save(task_simple_metadata)
    retrieved_simple = await vertex_store.get('task-metadata-test-2')
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
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=complex_metadata,
    )
    await vertex_store.save(task_complex_metadata)
    retrieved_complex = await vertex_store.get('task-metadata-test-3')
    assert retrieved_complex is not None
    assert retrieved_complex.metadata == complex_metadata

    # Test 4: Update metadata from None to dict
    task_update_metadata = Task(
        id='task-metadata-test-4',
        context_id='session-meta-4',
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=None,
    )
    await vertex_store.save(task_update_metadata)

    # Update metadata
    task_update_metadata.metadata = {'updated': True, 'timestamp': '2024-01-01'}
    await vertex_store.save(task_update_metadata)

    retrieved_updated = await vertex_store.get('task-metadata-test-4')
    assert retrieved_updated is not None
    assert retrieved_updated.metadata == {
        'updated': True,
        'timestamp': '2024-01-01',
    }

    # Test 5: Update metadata from dict to None
    task_update_metadata.metadata = None
    await vertex_store.save(task_update_metadata)

    retrieved_none = await vertex_store.get('task-metadata-test-4')
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
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=None,
        artifacts=[],
        history=[],
    )
    await vertex_store.save(original_task)

    retrieved_before_update = await vertex_store.get(task_id)
    assert retrieved_before_update is not None
    assert retrieved_before_update.status.message is None

    updated_task = original_task.model_copy(deep=True)
    updated_task.status.state = TaskState.failed
    updated_task.status.timestamp = '2023-01-02T11:00:00Z'
    updated_task.status.message = Message(
        message_id='msg-error-1',
        role=Role.agent,
        parts=[
            Part(
                root=TextPart(
                    text='Task failed due to an unknown error',
                    metadata={'error_code': 'UNKNOWN', 'retryable': False},
                )
            )
        ],
    )

    await vertex_store.save(updated_task)

    retrieved_after_update = await vertex_store.get(task_id)
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.failed
    assert retrieved_after_update.status.message is not None
    assert retrieved_after_update.status.message.message_id == 'msg-error-1'
    assert retrieved_after_update.status.message.role == Role.agent
    assert len(retrieved_after_update.status.message.parts) == 1

    assert isinstance(
        retrieved_after_update.status.message.parts[0].root, TextPart
    )
    text_part = retrieved_after_update.status.message.parts[0].root
    assert text_part.text == 'Task failed due to an unknown error'
    assert text_part.metadata == {'error_code': 'UNKNOWN', 'retryable': False}

    # Also test clearing the message
    cleared_task = updated_task.model_copy(deep=True)
    cleared_task.status.message = None

    await vertex_store.save(cleared_task)
    retrieved_cleared = await vertex_store.get(task_id)
    assert retrieved_cleared is not None
    assert retrieved_cleared.status.message is None
