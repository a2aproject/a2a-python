"""Fake Vertex AI Client implementations for testing."""

import copy

from google.genai import errors as genai_errors
from vertexai import types as vertexai_types


class FakeAgentEnginesA2aTasksEventsClient:
    def __init__(self, parent_client):
        self.parent_client = parent_client

    async def append(
        self, name: str, task_events: list[vertexai_types.TaskEvent]
    ) -> None:
        task = self.parent_client.tasks.get(name)
        if not task:
            raise genai_errors.APIError(
                code=404,
                response_json={
                    'error': {
                        'status': 'NOT_FOUND',
                        'message': 'Task not found',
                    }
                },
            )

        task = copy.deepcopy(task)
        if (
            not hasattr(task, 'next_event_sequence_number')
            or not task.next_event_sequence_number
        ):
            task.next_event_sequence_number = 0

        for event in task_events:
            data = event.event_data
            if getattr(data, 'state_change', None):
                task.state = getattr(data.state_change, 'new_state', task.state)
            if getattr(data, 'metadata_change', None):
                task.metadata = getattr(
                    data.metadata_change, 'new_metadata', task.metadata
                )
            if getattr(data, 'output_change', None):
                change = getattr(
                    data.output_change, 'task_artifact_change', None
                )
                if not change:
                    continue
                if not getattr(task, 'output', None):
                    task.output = vertexai_types.TaskOutput()

                current_artifacts = (
                    list(task.output.artifacts)
                    if getattr(task.output, 'artifacts', None)
                    else []
                )

                deleted_ids = getattr(change, 'deleted_artifact_ids', []) or []
                if deleted_ids:
                    current_artifacts = [
                        a
                        for a in current_artifacts
                        if a.artifact_id not in deleted_ids
                    ]

                added = getattr(change, 'added_artifacts', []) or []
                if added:
                    current_artifacts.extend(added)

                updated = getattr(change, 'updated_artifacts', []) or []
                if updated:
                    updated_map = {a.artifact_id: a for a in updated}
                    current_artifacts = [
                        updated_map.get(a.artifact_id, a)
                        for a in current_artifacts
                    ]

                try:
                    del task.output.artifacts[:]
                    task.output.artifacts.extend(current_artifacts)
                except Exception:
                    task.output.artifacts = current_artifacts
            task.next_event_sequence_number += 1

        self.parent_client.tasks[name] = task


class FakeAgentEnginesA2aTasksClient:
    def __init__(self):
        self.tasks: dict[str, vertexai_types.A2aTask] = {}
        self.events = FakeAgentEnginesA2aTasksEventsClient(self)

    async def create(
        self,
        name: str,
        a2a_task_id: str,
        config: vertexai_types.CreateAgentEngineTaskConfig,
    ) -> vertexai_types.A2aTask:
        full_name = f'{name}/a2aTasks/{a2a_task_id}'
        task = vertexai_types.A2aTask(
            name=full_name,
            context_id=config.context_id,
            metadata=config.metadata,
            output=config.output,
            state=vertexai_types.State.SUBMITTED,
        )
        task.next_event_sequence_number = 1
        self.tasks[full_name] = task
        return task

    async def get(self, name: str) -> vertexai_types.A2aTask:
        if name not in self.tasks:
            raise genai_errors.APIError(
                code=404,
                response_json={
                    'error': {
                        'status': 'NOT_FOUND',
                        'message': 'Task not found',
                    }
                },
            )
        return copy.deepcopy(self.tasks[name])


class FakeAgentEnginesClient:
    def __init__(self):
        self.a2a_tasks = FakeAgentEnginesA2aTasksClient()


class FakeAioClient:
    def __init__(self):
        self.agent_engines = FakeAgentEnginesClient()


class FakeVertexClient:
    def __init__(self):
        self.aio = FakeAioClient()
