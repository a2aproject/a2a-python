"""General utility functions for the A2A Python SDK."""

import functools
import inspect
import json
import logging

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from a2a.types import (
    AgentCard,
    Artifact,
    MessageSendParams,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TextPart,
)
from a2a.utils.errors import ServerError, UnsupportedOperationError
from a2a.utils.telemetry import trace_function


logger = logging.getLogger(__name__)


@trace_function()
def create_task_obj(message_send_params: MessageSendParams) -> Task:
    """Create a new task object from message send params.

    Generates UUIDs for task and context IDs if they are not already present in the message.

    Args:
        message_send_params: The `MessageSendParams` object containing the initial message.

    Returns:
        A new `Task` object initialized with 'submitted' status and the input message in history.
    """
    if not message_send_params.message.context_id:
        message_send_params.message.context_id = str(uuid4())

    return Task(
        id=str(uuid4()),
        context_id=message_send_params.message.context_id,
        status=TaskStatus(state=TaskState.submitted),
        history=[message_send_params.message],
    )


@trace_function()
def append_artifact_to_task(task: Task, event: TaskArtifactUpdateEvent) -> None:
    """Helper method for updating a Task object with new artifact data from an event.

    Handles creating the artifacts list if it doesn't exist, adding new artifacts,
    and appending parts to existing artifacts based on the `append` flag in the event.

    Args:
        task: The `Task` object to modify.
        event: The `TaskArtifactUpdateEvent` containing the artifact data.
    """
    if not task.artifacts:
        task.artifacts = []

    new_artifact_data: Artifact = event.artifact
    artifact_id: str = new_artifact_data.artifact_id
    append_parts: bool = event.append or False

    existing_artifact: Artifact | None = None
    existing_artifact_list_index: int | None = None

    # Find existing artifact by its id
    for i, art in enumerate(task.artifacts):
        if art.artifact_id == artifact_id:
            existing_artifact = art
            existing_artifact_list_index = i
            break

    if not append_parts:
        # This represents the first chunk for this artifact index.
        if existing_artifact_list_index is not None:
            # Replace the existing artifact entirely with the new data
            logger.debug(
                'Replacing artifact at id %s for task %s', artifact_id, task.id
            )
            task.artifacts[existing_artifact_list_index] = new_artifact_data
        else:
            # Append the new artifact since no artifact with this index exists yet
            logger.debug(
                'Adding new artifact with id %s for task %s',
                artifact_id,
                task.id,
            )
            task.artifacts.append(new_artifact_data)
    elif existing_artifact:
        # Append new parts to the existing artifact's part list
        logger.debug(
            'Appending parts to artifact id %s for task %s',
            artifact_id,
            task.id,
        )
        existing_artifact.parts.extend(new_artifact_data.parts)
    else:
        # We received a chunk to append, but we don't have an existing artifact.
        # we will ignore this chunk
        logger.warning(
            'Received append=True for nonexistent artifact index %s in task %s. Ignoring chunk.',
            artifact_id,
            task.id,
        )


def build_text_artifact(text: str, artifact_id: str) -> Artifact:
    """Helper to create a text artifact.

    Args:
        text: The text content for the artifact.
        artifact_id: The ID for the artifact.

    Returns:
        An `Artifact` object containing a single `TextPart`.
    """
    text_part = TextPart(text=text)
    part = Part(root=text_part)
    return Artifact(parts=[part], artifact_id=artifact_id)


def validate(
    expression: Callable[[Any], bool], error_message: str | None = None
) -> Callable:
    """Decorator that validates if a given expression evaluates to True.

    Typically used on class methods to check capabilities or configuration
    before executing the method's logic. If the expression is False,
    a `ServerError` with an `UnsupportedOperationError` is raised.

    Args:
        expression: A callable that takes the instance (`self`) as its argument
                    and returns a boolean.
        error_message: An optional custom error message for the `UnsupportedOperationError`.
                       If None, the string representation of the expression will be used.

    Examples:
        Demonstrating with an async method:
        >>> import asyncio
        >>> from a2a.utils.errors import ServerError
        >>>
        >>> class MyAgent:
        ...     def __init__(self, streaming_enabled: bool):
        ...         self.streaming_enabled = streaming_enabled
        ...
        ...     @validate(
        ...         lambda self: self.streaming_enabled,
        ...         'Streaming is not enabled for this agent',
        ...     )
        ...     async def stream_response(self, message: str):
        ...         return f'Streaming: {message}'
        >>>
        >>> async def run_async_test():
        ...     # Successful call
        ...     agent_ok = MyAgent(streaming_enabled=True)
        ...     result = await agent_ok.stream_response('hello')
        ...     print(result)
        ...
        ...     # Call that fails validation
        ...     agent_fail = MyAgent(streaming_enabled=False)
        ...     try:
        ...         await agent_fail.stream_response('world')
        ...     except ServerError as e:
        ...         print(e.error.message)
        >>>
        >>> asyncio.run(run_async_test())
        Streaming: hello
        Streaming is not enabled for this agent

        Demonstrating with a sync method:
        >>> class SecureAgent:
        ...     def __init__(self):
        ...         self.auth_enabled = False
        ...
        ...     @validate(
        ...         lambda self: self.auth_enabled,
        ...         'Authentication must be enabled for this operation',
        ...     )
        ...     def secure_operation(self, data: str):
        ...         return f'Processing secure data: {data}'
        >>>
        >>> # Error case example
        >>> agent = SecureAgent()
        >>> try:
        ...     agent.secure_operation('secret')
        ... except ServerError as e:
        ...     print(e.error.message)
        Authentication must be enabled for this operation

    Note:
        This decorator works with both sync and async methods automatically.
    """

    def decorator(function: Callable) -> Callable:
        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def async_wrapper(self: Any, *args, **kwargs) -> Any:
                if not expression(self):
                    final_message = error_message or str(expression)
                    logger.error('Unsupported Operation: %s', final_message)
                    raise ServerError(
                        UnsupportedOperationError(message=final_message)
                    )
                return await function(self, *args, **kwargs)

            return async_wrapper

        @functools.wraps(function)
        def sync_wrapper(self: Any, *args, **kwargs) -> Any:
            if not expression(self):
                final_message = error_message or str(expression)
                logger.error('Unsupported Operation: %s', final_message)
                raise ServerError(
                    UnsupportedOperationError(message=final_message)
                )
            return function(self, *args, **kwargs)

        return sync_wrapper

    return decorator


def validate_async_generator(
    expression: Callable[[Any], bool], error_message: str | None = None
):
    """Decorator that validates if a given expression evaluates to True for async generators.

    Typically used on class methods to check capabilities or configuration
    before executing the method's logic. If the expression is False,
    a `ServerError` with an `UnsupportedOperationError` is raised.

    Args:
        expression: A callable that takes the instance (`self`) as its argument
                    and returns a boolean.
        error_message: An optional custom error message for the `UnsupportedOperationError`.
                       If None, the string representation of the expression will be used.

    Examples:
        Streaming capability validation with success case:
        >>> import asyncio
        >>> from a2a.utils.errors import ServerError
        >>>
        >>> class StreamingAgent:
        ...     def __init__(self, streaming_enabled: bool):
        ...         self.streaming_enabled = streaming_enabled
        ...
        ...     @validate_async_generator(
        ...         lambda self: self.streaming_enabled,
        ...         'Streaming is not supported by this agent',
        ...     )
        ...     async def stream_messages(self, count: int):
        ...         for i in range(count):
        ...             yield f'Message {i}'
        >>>
        >>> async def run_streaming_test():
        ...     # Successful streaming
        ...     agent = StreamingAgent(streaming_enabled=True)
        ...     async for msg in agent.stream_messages(2):
        ...         print(msg)
        >>>
        >>> asyncio.run(run_streaming_test())
        Message 0
        Message 1

        Error case - validation fails:
        >>> class FeatureAgent:
        ...     def __init__(self):
        ...         self.features = {'real_time': False}
        ...
        ...     @validate_async_generator(
        ...         lambda self: self.features.get('real_time', False),
        ...         'Real-time feature must be enabled to stream updates',
        ...     )
        ...     async def real_time_updates(self):
        ...         yield 'This should not be yielded'
        >>>
        >>> async def run_error_test():
        ...     agent = FeatureAgent()
        ...     try:
        ...         async for _ in agent.real_time_updates():
        ...             pass
        ...     except ServerError as e:
        ...         print(e.error.message)
        >>>
        >>> asyncio.run(run_error_test())
        Real-time feature must be enabled to stream updates

    Note:
        This decorator is specifically for async generator methods (async def with yield).
        The validation happens before the generator starts yielding values.
    """

    def decorator(function):
        @functools.wraps(function)
        async def wrapper(self, *args, **kwargs):
            if not expression(self):
                final_message = error_message or str(expression)
                logger.error('Unsupported Operation: %s', final_message)
                raise ServerError(
                    UnsupportedOperationError(message=final_message)
                )
            async for i in function(self, *args, **kwargs):
                yield i

        return wrapper

    return decorator


def are_modalities_compatible(
    server_output_modes: list[str] | None, client_output_modes: list[str] | None
) -> bool:
    """Checks if server and client output modalities (MIME types) are compatible.

    Modalities are compatible if:
    1. The client specifies no preferred output modes (client_output_modes is None or empty).
    2. The server specifies no supported output modes (server_output_modes is None or empty).
    3. There is at least one common modality between the server's supported list and the client's preferred list.

    Args:
        server_output_modes: A list of MIME types supported by the server/agent for output.
                             Can be None or empty if the server doesn't specify.
        client_output_modes: A list of MIME types preferred by the client for output.
                             Can be None or empty if the client accepts any.

    Returns:
        True if the modalities are compatible, False otherwise.
    """
    if client_output_modes is None or len(client_output_modes) == 0:
        return True

    if server_output_modes is None or len(server_output_modes) == 0:
        return True

    return any(x in server_output_modes for x in client_output_modes)


def _clean_empty(d: Any) -> Any:
    """Recursively remove empty strings, lists and dicts from a dictionary."""
    if isinstance(d, dict):
        cleaned_dict: dict[Any, Any] = {
            k: _clean_empty(v) for k, v in d.items()
        }
        return {k: v for k, v in cleaned_dict.items() if v}
    if isinstance(d, list):
        cleaned_list: list[Any] = [_clean_empty(v) for v in d]
        return [v for v in cleaned_list if v]
    return d if d not in ['', [], {}] else None


def canonicalize_agent_card(agent_card: AgentCard) -> str:
    """Canonicalizes the Agent Card JSON according to RFC 8785 (JCS)."""
    card_dict = agent_card.model_dump(
        exclude={'signatures'},
        exclude_defaults=True,
        exclude_none=True,
        by_alias=True,
    )
    # Recursively remove empty values
    cleaned_dict = _clean_empty(card_dict)
    return json.dumps(cleaned_dict, separators=(',', ':'), sort_keys=True)
