from dataclasses import dataclass, field
from typing import Any

from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)


@dataclass
class TaskBuilder:
    id: str = 'task-default'
    context_id: str = 'context-default'
    state: TaskState = TaskState.submitted
    kind: str = 'task'
    artifacts: list[Artifact] = field(default_factory=list)
    history: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_id(self, task_id: str) -> 'TaskBuilder':
        self.id = task_id
        return self

    def with_context_id(self, context_id: str) -> 'TaskBuilder':
        self.context_id = context_id
        return self

    def with_state(self, state: TaskState) -> 'TaskBuilder':
        self.state = state
        return self

    def with_metadata(self, **kwargs) -> 'TaskBuilder':
        self.metadata.update(kwargs)
        return self

    def with_history(self, *messages: Message) -> 'TaskBuilder':
        self.history.extend(messages)
        return self

    def with_artifacts(self, *artifacts: Artifact) -> 'TaskBuilder':
        self.artifacts.extend(artifacts)
        return self

    def build(self) -> Task:
        return Task(
            id=self.id,
            context_id=self.context_id,
            status=TaskStatus(state=self.state),
            kind=self.kind,
            artifacts=self.artifacts if self.artifacts else None,
            history=self.history if self.history else None,
            metadata=self.metadata if self.metadata else None,
        )


@dataclass
class MessageBuilder:
    role: Role = Role.user
    text: str = 'default message'
    message_id: str = 'msg-default'
    task_id: str | None = None
    context_id: str | None = None

    def as_agent(self) -> 'MessageBuilder':
        self.role = Role.agent
        return self

    def as_user(self) -> 'MessageBuilder':
        self.role = Role.user
        return self

    def with_text(self, text: str) -> 'MessageBuilder':
        self.text = text
        return self

    def with_id(self, message_id: str) -> 'MessageBuilder':
        self.message_id = message_id
        return self

    def with_task_id(self, task_id: str) -> 'MessageBuilder':
        self.task_id = task_id
        return self

    def with_context_id(self, context_id: str) -> 'MessageBuilder':
        self.context_id = context_id
        return self

    def build(self) -> Message:
        return Message(
            role=self.role,
            parts=[Part(TextPart(text=self.text))],
            message_id=self.message_id,
            task_id=self.task_id,
            context_id=self.context_id,
        )


@dataclass
class ArtifactBuilder:
    artifact_id: str = 'artifact-default'
    name: str = 'default artifact'
    text: str = 'default content'
    description: str | None = None

    def with_id(self, artifact_id: str) -> 'ArtifactBuilder':
        self.artifact_id = artifact_id
        return self

    def with_name(self, name: str) -> 'ArtifactBuilder':
        self.name = name
        return self

    def with_text(self, text: str) -> 'ArtifactBuilder':
        self.text = text
        return self

    def with_description(self, description: str) -> 'ArtifactBuilder':
        self.description = description
        return self

    def build(self) -> Artifact:
        return Artifact(
            artifact_id=self.artifact_id,
            name=self.name,
            parts=[Part(TextPart(text=self.text))],
            description=self.description,
        )


@dataclass
class StatusUpdateEventBuilder:
    task_id: str = 'task-default'
    context_id: str = 'context-default'
    state: TaskState = TaskState.working
    message: Message | None = None
    final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def for_task(self, task_id: str) -> 'StatusUpdateEventBuilder':
        self.task_id = task_id
        return self

    def with_state(self, state: TaskState) -> 'StatusUpdateEventBuilder':
        self.state = state
        return self

    def with_message(self, message: Message) -> 'StatusUpdateEventBuilder':
        self.message = message
        return self

    def as_final(self) -> 'StatusUpdateEventBuilder':
        self.final = True
        return self

    def with_metadata(self, **kwargs) -> 'StatusUpdateEventBuilder':
        self.metadata.update(kwargs)
        return self

    def build(self) -> TaskStatusUpdateEvent:
        return TaskStatusUpdateEvent(
            task_id=self.task_id,
            context_id=self.context_id,
            status=TaskStatus(state=self.state, message=self.message),
            final=self.final,
            metadata=self.metadata if self.metadata else None,
        )


@dataclass
class ArtifactUpdateEventBuilder:
    task_id: str = 'task-default'
    context_id: str = 'context-default'
    artifact: Artifact | None = None
    append: bool = False
    last_chunk: bool = False

    def for_task(self, task_id: str) -> 'ArtifactUpdateEventBuilder':
        self.task_id = task_id
        return self

    def with_artifact(self, artifact: Artifact) -> 'ArtifactUpdateEventBuilder':
        self.artifact = artifact
        return self

    def as_append(self) -> 'ArtifactUpdateEventBuilder':
        self.append = True
        return self

    def as_last_chunk(self) -> 'ArtifactUpdateEventBuilder':
        self.last_chunk = True
        return self

    def build(self) -> TaskArtifactUpdateEvent:
        artifact = self.artifact
        if not artifact:
            artifact = ArtifactBuilder().build()
        return TaskArtifactUpdateEvent(
            task_id=self.task_id,
            context_id=self.context_id,
            artifact=artifact,
            append=self.append,
            last_chunk=self.last_chunk,
        )
