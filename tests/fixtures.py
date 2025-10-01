import pytest

from a2a.server.tasks import TaskManager
from a2a.types import TaskState
from tests.builders import (
    ArtifactBuilder,
    MessageBuilder,
    TaskBuilder,
)
from tests.test_doubles import (
    FakeHttpClient,
    InMemoryTaskStore,
    SpyEventQueue,
    StubPushNotificationConfigStore,
)


@pytest.fixture
def task_store():
    return InMemoryTaskStore()


@pytest.fixture
def event_queue():
    return SpyEventQueue()


@pytest.fixture
def push_config_store():
    return StubPushNotificationConfigStore()


@pytest.fixture
def http_client():
    return FakeHttpClient()


@pytest.fixture
def task_builder():
    return TaskBuilder()


@pytest.fixture
def message_builder():
    return MessageBuilder()


@pytest.fixture
def artifact_builder():
    return ArtifactBuilder()


@pytest.fixture
def submitted_task(task_builder):
    return task_builder.with_state(TaskState.submitted).build()


@pytest.fixture
def working_task(task_builder):
    return task_builder.with_state(TaskState.working).build()


@pytest.fixture
def completed_task(task_builder):
    return task_builder.with_state(TaskState.completed).build()


@pytest.fixture
def task_with_history(task_builder):
    messages = [
        MessageBuilder().as_user().with_text('Hello').build(),
        MessageBuilder().as_agent().with_text('Hi there!').build(),
    ]
    return task_builder.with_history(*messages).build()


@pytest.fixture
def task_with_artifacts(task_builder):
    artifacts = [
        ArtifactBuilder().with_id('art1').with_name('file.txt').build(),
        ArtifactBuilder().with_id('art2').with_name('data.json').build(),
    ]
    return task_builder.with_artifacts(*artifacts).build()


@pytest.fixture
def task_manager(task_store):
    return TaskManager(
        task_id='task-123',
        context_id='context-456',
        task_store=task_store,
        initial_message=None,
    )


@pytest.fixture
def task_manager_factory(task_store):
    def factory(task_id=None, context_id=None, initial_message=None):
        return TaskManager(
            task_id=task_id,
            context_id=context_id,
            task_store=task_store,
            initial_message=initial_message,
        )

    return factory


@pytest.fixture
def populated_task_store(task_store):
    tasks = [
        TaskBuilder().with_id('task-1').with_state(TaskState.submitted).build(),
        TaskBuilder().with_id('task-2').with_state(TaskState.working).build(),
        TaskBuilder().with_id('task-3').with_state(TaskState.completed).build(),
    ]
    for task in tasks:
        task_store.set_task(task)
    return task_store
