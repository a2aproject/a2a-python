import logging


try:
    import vertexai

    from google.genai import errors as genai_errors
    from vertexai import types as vertexai_types
except ImportError as e:
    raise ImportError(
        'VertexTaskStore requires vertexai. '
        'Install with: '
        "'pip install a2a-sdk[vertex]'"
    ) from e

from a2a.compat.v0_3.conversions import to_compat_task, to_core_task
from a2a.compat.v0_3.types import Task as CompatTask
from a2a.contrib.tasks import vertex_task_converter
from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task


logger = logging.getLogger(__name__)


class VertexTaskStore(TaskStore):
    """Implementation of TaskStore using Vertex AI Agent Engine Task Store.

    Stores task objects in Vertex AI Agent Engine Task Store.
    """

    def __init__(
        self,
        client: vertexai.Client,  # type: ignore
        agent_engine_resource_id: str,
    ) -> None:
        """Initializes the VertexTaskStore.

        Args:
            client: The Vertex AI client.
            agent_engine_resource_id: The resource ID of the agent engine.
        """
        self._client = client
        self._agent_engine_resource_id = agent_engine_resource_id

    async def save(self, task: Task, context: ServerCallContext) -> None:
        """Saves or updates a task in the store."""
        compat_task = to_compat_task(task)
        previous_task = await self._get_stored_task(compat_task.id)
        if previous_task is None:
            await self._create(compat_task)
        else:
            await self._update(previous_task, compat_task)

    async def _create(self, sdk_task: CompatTask) -> None:
        stored_task = vertex_task_converter.to_stored_task(sdk_task)
        await self._client.aio.agent_engines.a2a_tasks.create(
            name=self._agent_engine_resource_id,
            a2a_task_id=sdk_task.id,
            config=vertexai_types.CreateAgentEngineTaskConfig(
                context_id=stored_task.context_id,
                metadata=stored_task.metadata,
                output=stored_task.output,
            ),
        )

    def _get_status_change_event(
        self,
        previous_task: CompatTask,
        task: CompatTask,
        event_sequence_number: int,
    ) -> vertexai_types.TaskEvent | None:
        if task.status.state != previous_task.status.state:
            return vertexai_types.TaskEvent(
                event_data=vertexai_types.TaskEventData(
                    state_change=vertexai_types.TaskStateChange(
                        new_state=vertex_task_converter.to_stored_task_state(
                            task.status.state
                        ),
                    ),
                ),
                event_sequence_number=event_sequence_number,
            )
        return None

    def _get_status_details_change_event(
        self,
        previous_task: CompatTask,
        task: CompatTask,
        event_sequence_number: int,
    ) -> vertexai_types.TaskEvent | None:
        if task.status.message != previous_task.status.message:
            status_details = (
                vertexai_types.TaskStatusDetails(
                    task_message=vertex_task_converter.to_stored_message(
                        task.status.message
                    )
                )
                if task.status.message
                else vertexai_types.TaskStatusDetails()
            )
            return vertexai_types.TaskEvent(
                event_data=vertexai_types.TaskEventData(
                    status_details_change=vertexai_types.TaskStatusDetailsChange(
                        new_task_status=status_details,
                    ),
                ),
                event_sequence_number=event_sequence_number,
            )
        return None

    def _get_metadata_change_event(
        self,
        previous_task: CompatTask,
        task: CompatTask,
        event_sequence_number: int,
    ) -> vertexai_types.TaskEvent | None:
        # We generate metadata change events if the metadata was changed.
        # We don't generate events if the metadata was changed from
        # one empty value to another, e.g. {} to None.
        if task.metadata != previous_task.metadata and (
            task.metadata or previous_task.metadata
        ):
            return vertexai_types.TaskEvent(
                event_data=vertexai_types.TaskEventData(
                    metadata_change=vertexai_types.TaskMetadataChange(
                        new_metadata=task.metadata,
                    )
                ),
                event_sequence_number=event_sequence_number,
            )
        return None

    def _get_artifacts_change_event(
        self,
        previous_task: CompatTask,
        task: CompatTask,
        event_sequence_number: int,
    ) -> vertexai_types.TaskEvent | None:
        if task.artifacts != previous_task.artifacts:
            task_artifact_change = vertexai_types.TaskArtifactChange()
            event = vertexai_types.TaskEvent(
                event_data=vertexai_types.TaskEventData(
                    output_change=vertexai_types.TaskOutputChange(
                        task_artifact_change=task_artifact_change
                    )
                ),
                event_sequence_number=event_sequence_number,
            )
            task_artifacts = (
                {artifact.artifact_id: artifact for artifact in task.artifacts}
                if task.artifacts
                else {}
            )
            previous_task_artifacts = (
                {
                    artifact.artifact_id: artifact
                    for artifact in previous_task.artifacts
                }
                if previous_task.artifacts
                else {}
            )
            for artifact in previous_task_artifacts.values():
                if artifact.artifact_id not in task_artifacts:
                    if not task_artifact_change.deleted_artifact_ids:
                        task_artifact_change.deleted_artifact_ids = []
                    task_artifact_change.deleted_artifact_ids.append(
                        artifact.artifact_id
                    )
            for artifact in task_artifacts.values():
                if artifact.artifact_id not in previous_task_artifacts:
                    if not task_artifact_change.added_artifacts:
                        task_artifact_change.added_artifacts = []
                    task_artifact_change.added_artifacts.append(
                        vertex_task_converter.to_stored_artifact(artifact)
                    )
                elif artifact != previous_task_artifacts[artifact.artifact_id]:
                    if not task_artifact_change.updated_artifacts:
                        task_artifact_change.updated_artifacts = []
                    task_artifact_change.updated_artifacts.append(
                        vertex_task_converter.to_stored_artifact(artifact)
                    )
            if task_artifact_change != vertexai_types.TaskArtifactChange():
                return event
        return None

    async def _update(
        self, previous_stored_task: vertexai_types.A2aTask, task: CompatTask
    ) -> None:
        previous_task = vertex_task_converter.to_sdk_task(previous_stored_task)
        events = []
        event_sequence_number = previous_stored_task.next_event_sequence_number

        status_event = self._get_status_change_event(
            previous_task, task, event_sequence_number
        )
        if status_event:
            events.append(status_event)
            event_sequence_number += 1

        status_details_event = self._get_status_details_change_event(
            previous_task, task, event_sequence_number
        )
        if status_details_event:
            events.append(status_details_event)
            event_sequence_number += 1

        metadata_event = self._get_metadata_change_event(
            previous_task, task, event_sequence_number
        )
        if metadata_event:
            events.append(metadata_event)
            event_sequence_number += 1

        artifacts_event = self._get_artifacts_change_event(
            previous_task, task, event_sequence_number
        )
        if artifacts_event:
            events.append(artifacts_event)
            event_sequence_number += 1

        if not events:
            return
        await self._client.aio.agent_engines.a2a_tasks.events.append(
            name=self._agent_engine_resource_id + '/a2aTasks/' + task.id,
            task_events=events,
        )

    async def _get_stored_task(
        self, task_id: str
    ) -> vertexai_types.A2aTask | None:
        try:
            a2a_task = await self._client.aio.agent_engines.a2a_tasks.get(
                name=self._agent_engine_resource_id + '/a2aTasks/' + task_id,
            )
        except genai_errors.APIError as e:
            if e.status == 'NOT_FOUND':
                logger.debug('Task %s not found in store.', task_id)
                return None
            raise
        return a2a_task

    async def get(
        self, task_id: str, context: ServerCallContext
    ) -> Task | None:
        """Retrieves a task from the database by ID."""
        a2a_task = await self._get_stored_task(task_id)
        if a2a_task is None:
            return None
        return to_core_task(vertex_task_converter.to_sdk_task(a2a_task))

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """Retrieves a list of tasks from the store."""
        raise NotImplementedError

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """The backend doesn't support deleting tasks, so this is not implemented."""
        raise NotImplementedError
