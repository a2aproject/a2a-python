"""Stdio transport implementation for A2A.

This transport spawns a subprocess and communicates using line-delimited JSON
messages over the process's stdin/stdout. It implements the `ClientTransport`
interface but currently provides stubbed implementations for most RPC methods.

MVP scope:
 - Spawn process
 - Basic send_message support (non-streaming)
 - Graceful close
 - Background reader loop skeleton

Follow-up work will implement streaming, task management methods, interceptors,
error propagation, and telemetry integration.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from a2a.client.errors import (
    A2AClientError,
    A2AClientInvalidStateError,
    A2AClientJSONError,
)


if TYPE_CHECKING:  # Only for type checking to avoid runtime import cycles
    from collections.abc import AsyncGenerator

    from a2a.client.middleware import ClientCallContext
from a2a.client.transports.base import ClientTransport
from a2a.types import (
    AgentCard,
    GetTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskStatusUpdateEvent,
)


logger = logging.getLogger(__name__)


@dataclass
class _PendingRequest:
    id: str
    future: asyncio.Future[Any]
    streaming: bool = False
    queue: asyncio.Queue[Any] | None = None  # for streaming responses


class StdioTransport(ClientTransport):
    """A stdio-based transport for the A2A client (initial scaffold)."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        agent_card: AgentCard | None = None,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.agent_card = agent_card
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, _PendingRequest] = {}
        self._stderr_task: asyncio.Task[None] | None = None
        self._closed = False
        self._start_lock = asyncio.Lock()

    async def _ensure_started(self) -> None:
        if self._process and self._process.returncode is None:
            return
        async with self._start_lock:
            if self._process and self._process.returncode is None:
                return
            logger.debug('Spawning stdio transport process: %s', self.command)
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=self.env,
            )
            self._reader_task = asyncio.create_task(self._reader_loop())
            self._stderr_task = asyncio.create_task(self._stderr_drain())

    async def _stderr_drain(self) -> None:
        proc = self._process
        if not proc or not proc.stderr:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                logger.debug(
                    '[stdio subprocess stderr] %s',
                    line.decode(errors='replace').rstrip(),
                )
        except (asyncio.CancelledError, OSError) as e:
            logger.debug('Stderr drain terminated: %s', e)

    async def _reader_loop(self) -> None:
        """Continuously reads stdout lines and dispatches responses to pending requests.

        Supports both unary and streaming responses. Streaming responses reuse the
        same id for multiple result objects. An end-of-stream is inferred when a
        pending request is marked streaming and its queue is drained externally
        via explicit completion or the subprocess terminates. (Future: adopt
        explicit end-of-stream marker field.)
        """
        proc = self._process
        if not proc or not proc.stdout:  # Early exit if process/pipe missing
            return

        async def _dispatch(line: str) -> None:
            if not line:
                return
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning('Malformed JSON from subprocess: %s', line)
                return
            msg_id = msg.get('id')
            if not msg_id:
                return
            pending = self._pending.get(msg_id)
            if not pending:
                return
            # End-of-stream marker
            if msg.get('eos') is True:
                if pending.streaming and pending.queue:
                    await pending.queue.put({'__eos__': True})
                # cleanup after signaling eos
                self._pending.pop(msg_id, None)
                return
            if pending.streaming:
                result = msg.get('result')
                if pending.queue and result is not None:
                    await pending.queue.put(result)
                return
            if not pending.future.done():
                pending.future.set_result(msg)
            self._pending.pop(msg_id, None)

        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break  # EOF
                await _dispatch(raw.decode('utf-8', errors='replace').strip())
        except (asyncio.CancelledError, OSError) as e:  # pragma: no cover
            logger.debug('Reader loop interrupted: %s', e)
        finally:
            if self._pending:
                err = A2AClientError('Subprocess terminated')
                for req in list(self._pending.values()):
                    if not req.future.done():
                        req.future.set_exception(err)
                self._pending.clear()

    async def _send_json(self, payload: dict[str, Any]) -> str:
        """Sends a single JSON object terminated by a newline to the subprocess."""
        await self._ensure_started()
        proc = self._process
        if not proc or not proc.stdin:
            raise A2AClientInvalidStateError('Process not started')
        msg_id = payload.get('id') or str(uuid.uuid4())
        payload['id'] = msg_id
        data = json.dumps(payload, separators=(',', ':')) + '\n'
        proc.stdin.write(data.encode('utf-8'))
        await proc.stdin.drain()
        return msg_id

    # --- ClientTransport interface methods ---
    async def send_message(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task | Message:
        """Sends a non-streaming message request (basic scaffold)."""
        # Prepare JSON-RPC-like payload
        payload = {
            'jsonrpc': '2.0',
            'method': 'message/send',
            'params': request.model_dump(mode='json', exclude_none=True),
        }
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        msg_id = await self._send_json(payload)
        self._pending[msg_id] = _PendingRequest(id=msg_id, future=fut)
        try:
            response = await fut
        except asyncio.CancelledError:
            raise
        except OSError as e:
            raise A2AClientJSONError(str(e)) from e
        # Minimal response mapping: expect {'result': {...}}
        if 'result' not in response:
            raise A2AClientJSONError('Missing result in response')
        # The server decides if it's Task or Message; we can't parse fully without schema.
        # Return raw result for now; future step: hydrate into proper model.
        result = response['result']
        # Attempt discriminating by presence of 'status' (Task) or 'content' (Message)
        if isinstance(result, dict) and 'status' in result:
            return Task.model_validate(result)
        if isinstance(result, dict) and 'content' in result:
            return Message.model_validate(result)
        # Fallback: treat as Message if possible else raise.
        try:
            return Message.model_validate(result)
        except Exception as e:
            raise A2AClientJSONError(f'Unknown result type: {e}') from e

    async def send_message_streaming(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[
        Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ]:
        """Streams message responses as they arrive from the subprocess.

        The underlying protocol reuses the request id for multiple result objects.
        This generator yields each hydrated Message/Task update until the caller
        cancels or transport closes. (Future enhancement: explicit end marker.)
        """
        payload = {
            'jsonrpc': '2.0',
            'method': 'message/stream',
            'params': request.model_dump(mode='json', exclude_none=True),
        }
        queue: asyncio.Queue[Any] = asyncio.Queue()
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        msg_id = await self._send_json(payload)
        self._pending[msg_id] = _PendingRequest(
            id=msg_id,
            future=fut,
            streaming=True,
            queue=queue,
        )

        try:
            while True:
                try:
                    item = await queue.get()
                except asyncio.CancelledError:
                    break
                if isinstance(item, dict) and item.get('__eos__'):
                    break
                # Hydrate item into appropriate model
                if isinstance(item, dict) and 'status' in item:
                    yield Task.model_validate(item)
                elif isinstance(item, dict) and 'parts' in item:
                    try:
                        yield Message.model_validate(item)
                    except ValueError as e:  # pragma: no cover
                        logger.warning(
                            'Failed to parse streaming message: %s', e
                        )
                else:  # Unknown event shape; ignore for now
                    logger.debug(
                        'Ignoring unknown streaming event shape: %s', item
                    )
        finally:
            # Cleanup pending entry if still present
            self._pending.pop(msg_id, None)

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Stub for retrieving a task (not implemented)."""
        raise NotImplementedError

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Stub for cancelling a task (not implemented)."""
        raise NotImplementedError

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Stub for setting a task push notification config (not implemented)."""
        raise NotImplementedError

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Stub for retrieving a task push notification config (not implemented)."""
        raise NotImplementedError

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[
        Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ]:
        """Resubscribes to a task stream, yielding updates for a given task id."""
        payload = {
            'jsonrpc': '2.0',
            'method': 'tasks/resubscribe',
            'params': request.model_dump(mode='json', exclude_none=True),
        }
        queue: asyncio.Queue[Any] = asyncio.Queue()
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        msg_id = await self._send_json(payload)
        self._pending[msg_id] = _PendingRequest(
            id=msg_id,
            future=fut,
            streaming=True,
            queue=queue,
        )
        try:
            while True:
                try:
                    item = await queue.get()
                except asyncio.CancelledError:
                    break
                if isinstance(item, dict) and item.get('__eos__'):
                    break
                if isinstance(item, dict) and 'status' in item:
                    yield Task.model_validate(item)
                elif isinstance(item, dict) and 'parts' in item:
                    try:
                        yield Message.model_validate(item)
                    except ValueError as e:  # pragma: no cover
                        logger.warning(
                            'Failed to parse resubscribe message: %s', e
                        )
                else:
                    logger.debug(
                        'Ignoring unknown resubscribe event shape: %s', item
                    )
        finally:
            self._pending.pop(msg_id, None)

    async def get_card(
        self,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Returns cached agent card (stdio retrieval not implemented yet)."""
        if not self.agent_card:
            raise A2AClientInvalidStateError(
                'Agent card retrieval over stdio not yet implemented'
            )
        return self.agent_card

    async def close(self) -> None:
        """Gracefully terminates the subprocess and cleans up pending requests."""
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()
        self._process = None
        for req in self._pending.values():
            if not req.future.done():
                req.future.set_exception(
                    A2AClientError('Transport closed before response')
                )
        self._pending.clear()
