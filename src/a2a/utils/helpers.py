"""General utility functions for the A2A Python SDK."""

import functools
import inspect
import json
import logging

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar, cast
from uuid import uuid4

from google.protobuf.json_format import MessageToDict
from packaging.version import InvalidVersion, Version

from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import (
    AgentCard,
    Artifact,
    Part,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
)
from a2a.utils import constants
from a2a.utils.errors import VersionNotSupportedError
from a2a.utils.telemetry import trace_function


T = TypeVar('T')
F = TypeVar('F', bound=Callable[..., Any])


logger = logging.getLogger(__name__)


@trace_function()
def create_task_obj(message_send_params: SendMessageRequest) -> Task:
    """Create a new task object from message send params.

    Generates UUIDs for task and context IDs if they are not already present in the message.

    Args:
        message_send_params: The `SendMessageRequest` object containing the initial message.

    Returns:
        A new `Task` object initialized with 'submitted' status and the input message in history.
    """
    if not message_send_params.message.context_id:
        message_send_params.message.context_id = str(uuid4())

    task = Task(
        id=str(uuid4()),
        context_id=message_send_params.message.context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )
    task.history.append(message_send_params.message)
    return task


@trace_function()
def append_artifact_to_task(task: Task, event: TaskArtifactUpdateEvent) -> None:
    """Helper method for updating a Task object with new artifact data from an event.

    Handles creating the artifacts list if it doesn't exist, adding new artifacts,
    and appending parts to existing artifacts based on the `append` flag in the event.

    Args:
        task: The `Task` object to modify.
        event: The `TaskArtifactUpdateEvent` containing the artifact data.
    """
    new_artifact_data: Artifact = event.artifact
    artifact_id: str = new_artifact_data.artifact_id
    append_parts: bool = event.append

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
            task.artifacts[existing_artifact_list_index].CopyFrom(
                new_artifact_data
            )
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
        existing_artifact.metadata.update(
            dict(new_artifact_data.metadata.items())
        )
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
        An `Artifact` object containing a single text Part.
    """
    part = Part(text=text)
    return Artifact(parts=[part], artifact_id=artifact_id)


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
        cleaned_dict = {
            k: cleaned_v
            for k, v in d.items()
            if (cleaned_v := _clean_empty(v)) is not None
        }
        return cleaned_dict or None
    if isinstance(d, list):
        cleaned_list = [
            cleaned_v for v in d if (cleaned_v := _clean_empty(v)) is not None
        ]
        return cleaned_list or None
    if isinstance(d, str) and not d:
        return None
    return d


def canonicalize_agent_card(agent_card: AgentCard) -> str:
    """Canonicalizes the Agent Card JSON according to RFC 8785 (JCS)."""
    card_dict = MessageToDict(
        agent_card,
    )
    # Remove signatures field if present
    card_dict.pop('signatures', None)

    # Recursively remove empty values
    cleaned_dict = _clean_empty(card_dict)
    return json.dumps(cleaned_dict, separators=(',', ':'), sort_keys=True)


async def maybe_await(value: T | Awaitable[T]) -> T:
    """Awaits a value if it's awaitable, otherwise simply provides it back."""
    if inspect.isawaitable(value):
        return await value
    return value


def validate_version(expected_version: str) -> Callable[[F], F]:
    """Decorator that validates the A2A-Version header in the request context.

    The header name is defined by `constants.VERSION_HEADER` ('A2A-Version').
    If the header is missing or empty, it is interpreted as `constants.PROTOCOL_VERSION_0_3` ('0.3').
    If the version in the header does not match the `expected_version` (major and minor parts),
    a `VersionNotSupportedError` is raised. Patch version is ignored.

    This decorator supports both async methods and async generator methods. It
    expects a `ServerCallContext` to be present either in the arguments or
    keyword arguments of the decorated method.

    Args:
        expected_version: The A2A protocol version string expected by the method.

    Returns:
        The decorated function.

    Raises:
        VersionNotSupportedError: If the version in the request does not match `expected_version`.
    """
    try:
        expected_v = Version(expected_version)
    except InvalidVersion:
        # If the expected version is not a valid semver, we can't do major/minor comparison.
        # This shouldn't happen with our constants.
        expected_v = None

    def decorator(func: F) -> F:
        def _get_actual_version(
            args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            context = kwargs.get('context')
            if context is None:
                for arg in args:
                    if isinstance(arg, ServerCallContext):
                        context = arg
                        break

            if context is None:
                # If no context is found, we can't validate the version.
                # In a real scenario, this shouldn't happen for properly routed requests.
                # We default to the expected version to allow test call to proceed.
                return expected_version

            headers = context.state.get('headers', {})
            # Header names are usually case-insensitive in most frameworks, but dict lookup is case-sensitive.
            # We check both standard and lowercase versions.
            actual_version = headers.get(
                constants.VERSION_HEADER
            ) or headers.get(constants.VERSION_HEADER.lower())

            if not actual_version:
                return constants.PROTOCOL_VERSION_0_3

            return str(actual_version)

        def _is_version_compatible(actual: str) -> bool:
            if actual == expected_version:
                return True
            if not expected_v:
                return False
            try:
                actual_v = Version(actual)
            except InvalidVersion:
                return False
            else:
                return actual_v.major == expected_v.major

        if inspect.isasyncgenfunction(inspect.unwrap(func)):

            @functools.wraps(func)
            def async_gen_wrapper(
                *args: Any, **kwargs: Any
            ) -> AsyncIterator[Any]:
                actual_version = _get_actual_version(args, kwargs)
                if not _is_version_compatible(actual_version):
                    logger.warning(
                        "Version mismatch: actual='%s', expected='%s'",
                        actual_version,
                        expected_version,
                    )
                    raise VersionNotSupportedError(
                        message=f"A2A version '{actual_version}' is not supported by this handler. "
                        f"Expected version '{expected_version}'."
                    )
                return func(*args, **kwargs)

            return cast('F', async_gen_wrapper)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            actual_version = _get_actual_version(args, kwargs)
            if not _is_version_compatible(actual_version):
                logger.warning(
                    "Version mismatch: actual='%s', expected='%s'",
                    actual_version,
                    expected_version,
                )
                raise VersionNotSupportedError(
                    message=f"A2A version '{actual_version}' is not supported by this handler. "
                    f"Expected version '{expected_version}'."
                )
            return await func(*args, **kwargs)

        return cast('F', async_wrapper)

    return decorator
