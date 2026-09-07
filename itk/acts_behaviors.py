"""The ACTS SUT behaviour contract (ACTS spec §11) for the ITK agent.

ACTS tests are declarative — they say what to send and what to expect — so the
agent under test has to produce a *deterministic* reply for each case. §11
does that with a message-prefix convention rather than a side-channel API: the
text of the first user message part names the behaviour, and the agent obeys.

This module owns that mapping. `itk/main.py` routes to it when the first user
message starts with ``tck-``, and otherwise falls through to the existing ITK
instruction path, so one agent binary serves both suites.

**The behaviour is a property of the task, not of the message.** A multi-turn
test opens with ``tck-multi-turn start`` and then sends plain ``here is more
input`` and ``done``; only the first message names the contract, so a
continuation has to recover it from the task's history. :func:`behavior_for`
handles both.

**`acts/sut-behaviors.yaml` is the list; this module is the implementation.**
They are separate on purpose — the YAML is what the SDK *claims*, the code is
what it *does*, and the runner checks one against the other by running tests.
So nothing here re-states the list: the name is read straight out of the
message with a regex, and a prefix that reaches :func:`_dispatch` without a
branch fails the task loudly. Keeping a second copy of the names here would
add a way for the claim and the behaviour to drift silently, which is the one
thing the split exists to prevent.

That regex is greedy to the word boundary, which gives longest-match for
free: `tck-artifact-file-url` beats `tck-artifact-file` without an ordered
table, and a typo like `tck-complet-task` is reported as an unimplemented
behaviour instead of falling through to the ITK path and failing there with
"no valid instruction".
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from google.protobuf import json_format
from google.protobuf.struct_pb2 import Struct, Value

import acts_client_parse

from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import Message, Part, Task, TaskState, TaskStatus


if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.events import EventQueue


logger = logging.getLogger(__name__)

#: Every behaviour name starts with this, and it is what tells the agent an
#: incoming message belongs to ACTS rather than to an ITK traversal.
PREFIX = 'tck-'

#: The word a multi-turn conversation ends on. Fixed by the corpus, which
#: sends exactly this to close `CORE-MULTI-001`, `CORE-MULTI-005` and
#: `CORE-HIST-002`.
MULTI_TURN_DONE = 'done'

#: How long `tck-long-running` stays in WORKING before completing. Short
#: enough not to dominate a run, long enough that a test polling for a
#: non-terminal state sees one: the corpus polls with `delay_ms: 2000` and
#: `max_attempts: 15`.
LONG_RUNNING_DELAY_S = 1.0

#: A behaviour name: `tck-` and one or more hyphen-joined lowercase words.
#: Greedy, so it stops at the first character that cannot be part of a name —
#: which is what makes `tck-artifact-file-url document` yield the full name
#: rather than `tck-artifact-file`.
_NAME = re.compile(r'^(tck-[a-z0-9]+(?:-[a-z0-9]+)*)')


def _first_text(message: Message | None) -> str:
    """The first text part of a message, or ``''``."""
    if message is None:
        return ''
    for part in message.parts:
        if part.text:
            return part.text
    return ''


def behavior_in(text: str) -> str | None:
    """The behaviour named by ``text``, or ``None``.

    Names an *asserted* behaviour, not necessarily an implemented one: an
    unknown `tck-*` still routes here, and :func:`_dispatch` reports it as
    unimplemented. That is the honest outcome — the alternative is a message
    plainly meant for ACTS being handed to the traversal decoder.
    """
    match = _NAME.match(text.strip())
    return match.group(1) if match else None


def behavior_for(context: RequestContext) -> str | None:
    """The behaviour this request belongs to, current message or task.

    A continuation turn carries no prefix, so when the incoming message names
    none, the task's own history is consulted — its first user message is
    where the contract was declared.
    """
    named = behavior_in(_first_text(context.message))
    if named is not None:
        return named

    task = context.current_task
    if task is None:
        return None
    for historical in task.history:
        found = behavior_in(_first_text(historical))
        if found is not None:
            return found
    return None


def is_acts_request(context: RequestContext) -> bool:
    """Should this request be served by the ACTS contract rather than ITK?"""
    return behavior_for(context) is not None


def _data_part(payload: dict[str, Any]) -> Part:
    """A data part. `Part.data` is a `Value`, not a `Struct`."""
    struct = Struct()
    struct.update(payload)
    return Part(data=Value(struct_value=struct))


async def run(
    behavior: str,
    context: RequestContext,
    event_queue: EventQueue,
) -> None:
    """Serve one ACTS request, start to finish.

    Owns the task lifecycle rather than being handed a live task, because
    `tck-message-response` must produce **no** task at all — A2A lets an agent
    answer with a bare `Message`, and a server that opened a task first would
    make the response a task update instead, which is the opposite of what
    `CORE-SEND-003` checks.
    """
    logger.info('ACTS behaviour %s on task %s', behavior, context.task_id)

    if behavior == 'tck-message-response':
        await event_queue.enqueue_event(
            Message(
                role='ROLE_AGENT',
                message_id=str(uuid.uuid4()),
                context_id=context.context_id or '',
                parts=[Part(text='tck message response')],
            )
        )
        return

    updater = TaskUpdater(event_queue, context.task_id, context.context_id)

    # A continuation turn already has a task; re-announcing it would emit a
    # second submitted event for the same id.
    if context.current_task is None:
        task = Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            history=[context.message] if context.message else [],
        )
        async with updater._lock:  # noqa: SLF001
            await event_queue.enqueue_event(task)

    await updater.update_status(TaskState.TASK_STATE_WORKING)
    await _dispatch(behavior, context, updater)


async def _dispatch(
    behavior: str,
    context: RequestContext,
    updater: TaskUpdater,
) -> None:
    """Take an already-working task to wherever the behaviour ends.

    Unknown behaviours fail the task loudly rather than completing it — a
    silent success would report conformance the agent never demonstrated.
    """
    if behavior == acts_client_parse.BEHAVIOR:
        await _client_parse(context, updater)
        return

    if behavior == 'tck-multi-turn':
        await _multi_turn(context, updater)
        return

    if behavior == 'tck-cancel':
        # Hold in WORKING. The framework cancels the executor task when a
        # CancelTask arrives, which surfaces here as CancelledError.
        try:
            while True:
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            logger.info('tck-cancel: task %s canceled', context.task_id)
            raise
        return

    if behavior == 'tck-long-running':
        await asyncio.sleep(LONG_RUNNING_DELAY_S)
        # `CORE-EXEC-001` polls to completion and then asserts the finished
        # task carries at least one artifact, so the work has to leave one
        # behind even though §11.2 describes this behaviour only as "delayed
        # completion".
        await updater.add_artifact(
            [Part(text='long running result')],
            name='long-running',
            last_chunk=True,
        )
        await updater.complete(
            updater.new_agent_message([Part(text='long running work finished')])
        )
        return

    if behavior in ('tck-stream-basic', 'tck-stream-chunked'):
        await _stream(behavior, updater)
        return

    if behavior.startswith('tck-artifact-'):
        await _artifact(behavior, updater)
        return

    terminal = {
        'tck-complete-task': updater.complete,
        'tck-task-failure': updater.failed,
        'tck-reject-task': updater.reject,
        'tck-input-required': updater.requires_input,
        'tck-auth-required': updater.requires_auth,
    }.get(behavior)

    if terminal is None:
        await updater.failed(
            updater.new_agent_message(
                [Part(text=f'unimplemented ACTS behaviour {behavior!r}')]
            )
        )
        return

    await terminal(updater.new_agent_message([Part(text=f'{behavior} ok')]))


async def _client_parse(context: RequestContext, updater: TaskUpdater) -> None:
    """ACTS §10: run a canonical wire payload through this SDK's own client.

    The request carries `{operation, wire_payload}` in a data part; the reply
    carries whatever the client parsed, in the same data-part shape, so the
    runner can assert `expect_parsed` against it exactly as it would
    `expect.body`.
    """
    request = None
    for part in (context.message.parts if context.message else ()):
        if part.HasField('data'):
            request = acts_client_parse.request_from(
                json_format.MessageToDict(part.data)
                if hasattr(part.data, 'DESCRIPTOR')
                else part.data
            )
            if request is not None:
                break

    if request is None:
        await updater.failed(
            updater.new_agent_message(
                [Part(text='tck-client-parse needs {operation, wire_payload}')]
            )
        )
        return

    operation, payload = request
    parsed = await acts_client_parse.parse(operation, payload)
    await updater.add_artifact(
        [_data_part(parsed)], name=acts_client_parse.BEHAVIOR, last_chunk=True
    )
    await updater.complete(
        updater.new_agent_message([Part(text=f'{operation} parsed')])
    )


async def _multi_turn(context: RequestContext, updater: TaskUpdater) -> None:
    """INPUT_REQUIRED until the user says `done`, then COMPLETED."""
    said = _first_text(context.message).strip().lower()
    if said.startswith(MULTI_TURN_DONE):
        await updater.complete(
            updater.new_agent_message([Part(text='multi-turn complete')])
        )
        return
    await updater.requires_input(
        updater.new_agent_message([Part(text='more input please')])
    )


async def _artifact(behavior: str, updater: TaskUpdater) -> None:
    """Complete with the artifact shape the behaviour names."""
    parts = {
        'tck-artifact-text': [Part(text='generated text content')],
        'tck-artifact-data': [_data_part({'key': 'value', 'count': 1})],
        'tck-artifact-file': [
            Part(
                raw=b'file bytes',
                filename='document.txt',
                media_type='text/plain',
            )
        ],
        'tck-artifact-file-url': [
            Part(
                url='https://example.com/document.txt',
                filename='document.txt',
                media_type='text/plain',
            )
        ],
    }[behavior]

    await updater.add_artifact(parts, name=behavior, last_chunk=True)
    await updater.complete(
        updater.new_agent_message([Part(text=f'{behavior} ok')])
    )


async def _stream(behavior: str, updater: TaskUpdater) -> None:
    """working -> artifact(s) -> completed, as separate events.

    Emitted through the updater so each step is its own queue event, which is
    what makes them separate SSE frames — a single combined update would
    satisfy `min_count` only by accident.
    """
    await updater.update_status(
        TaskState.TASK_STATE_WORKING,
        message=updater.new_agent_message([Part(text='streaming started')]),
    )

    if behavior == 'tck-stream-chunked':
        artifact_id = f'{updater.task_id}-chunked'
        chunks = ['chunk one ', 'chunk two ', 'chunk three']
        for index, chunk in enumerate(chunks):
            await updater.add_artifact(
                [Part(text=chunk)],
                artifact_id=artifact_id,
                name='chunked',
                append=index > 0,
                last_chunk=index == len(chunks) - 1,
            )
    else:
        await updater.add_artifact(
            [Part(text='streamed content')], name='streamed', last_chunk=True
        )

    await updater.complete(
        updater.new_agent_message([Part(text=f'{behavior} ok')])
    )
