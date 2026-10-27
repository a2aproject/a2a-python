"""Exercises a running replica cluster across different replicas.

Deliberately spreads one task's requests across replicas to show that, with a
shared store + event stream, a task started on replica 0 can be observed from
replica 1 and cancelled from replica 2 - consistently, because no per-task
state is pinned to a single replica. Uses raw JSON-RPC over httpx with protojson
bodies, so it depends only on the SDK's proto types.

Usage (after run_cluster.py is up):
    python -m samples.clustermode.exercise --host 127.0.0.1 --ports 41241,41242
"""

import argparse
import asyncio
import contextlib
import json
import uuid

from typing import Any

import httpx

from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import Message as ProtoMessage

from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
)


MIN_REPLICAS = 2
_WORKING = TaskState.TASK_STATE_WORKING
_CANCELED = TaskState.TASK_STATE_CANCELED


def _endpoint(host: str, port: int) -> str:
    return f'http://{host}:{port}/a2a/jsonrpc'


async def _rpc(
    client: httpx.AsyncClient,
    url: str,
    method: str,
    params_msg: ProtoMessage,
) -> dict[str, Any]:
    """Sends one JSON-RPC call with a protojson params body; returns result."""
    body = {
        'jsonrpc': '2.0',
        'id': str(uuid.uuid4()),
        'method': method,
        'params': MessageToDict(params_msg),
    }
    # This sample speaks the v1.0 protocol; the header selects the v1 handler.
    resp = await client.post(
        url, json=body, headers={'A2A-Version': '1.0'}, timeout=30.0
    )
    resp.raise_for_status()
    data = resp.json()
    if 'error' in data:
        raise RuntimeError(f'{method} error: {data["error"]}')
    return data['result']


async def _get_task(client: httpx.AsyncClient, url: str, task_id: str) -> Task:
    """Fetches a task via GetTask from the given replica."""
    result = await _rpc(client, url, 'GetTask', GetTaskRequest(id=task_id))
    task = Task()
    ParseDict(result, task)
    return task


async def _start_streaming(
    client: httpx.AsyncClient, url: str, send: SendMessageRequest
) -> tuple[str, asyncio.Task[None]]:
    """Starts a streaming send; returns (task_id, drainer task).

    The drainer keeps the SSE stream open (so the agent keeps running) until it
    is cancelled. The task id is taken from the first streamed Task event.
    """
    loop = asyncio.get_event_loop()
    task_id_future: asyncio.Future[str] = loop.create_future()

    async def _drain() -> None:
        body = {
            'jsonrpc': '2.0',
            'id': str(uuid.uuid4()),
            'method': 'SendStreamingMessage',
            'params': MessageToDict(send),
        }
        headers = {'A2A-Version': '1.0', 'Accept': 'text/event-stream'}
        with contextlib.suppress(Exception):
            async with client.stream(
                'POST', url, json=body, headers=headers, timeout=120.0
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith('data:'):
                        continue
                    payload = json.loads(line[len('data:') :].strip())
                    result = payload.get('result')
                    if result is None:
                        continue
                    sr = StreamResponse()
                    ParseDict(result, sr)
                    if not task_id_future.done() and sr.HasField('task'):
                        task_id_future.set_result(sr.task.id)

    drainer = asyncio.create_task(_drain())
    try:
        task_id = await asyncio.wait_for(task_id_future, timeout=30)
    except TimeoutError:
        drainer.cancel()
        raise RuntimeError('no task event received from stream') from None
    return task_id, drainer


async def run(host: str, ports: list[int]) -> None:
    """Runs the cross-replica send / observe / cancel demonstration."""
    if len(ports) < MIN_REPLICAS:
        raise SystemExit('Need at least 2 replicas to demonstrate the point.')

    urls = [_endpoint(host, p) for p in ports]

    async with httpx.AsyncClient() as client:
        # 1) Start a streaming task on replica 0. The stream stays open (agent
        #    runs) while we observe and cancel from other replicas.
        print(f'[send]      -> replica 0 ({urls[0]}) (streaming)')
        send = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                message_id=str(uuid.uuid4()),
                parts=[Part(text='hello cluster')],
            )
        )
        task_id, send_call = await _start_streaming(client, urls[0], send)
        print(f'            task {task_id} started')

        # 2) Poll GetTask from replica 1 until it observes WORKING.
        print(f'[get]       -> replica 1 ({urls[1]}) while it runs')
        seen_working = False
        for _ in range(8):
            await asyncio.sleep(1.0)
            try:
                task = await _get_task(client, urls[1], task_id)
            except RuntimeError:
                continue  # not persisted yet
            print(
                f'            replica 1 sees state='
                f'{TaskState.Name(task.status.state)}'
            )
            if task.status.state == _WORKING:
                seen_working = True
                break
        if not seen_working:
            raise RuntimeError('replica 1 never observed the task WORKING')

        # 3) Cancel from a different replica than the one running the agent.
        cancel_idx = 2 if len(urls) > MIN_REPLICAS else 1
        print(f'[cancel]    -> replica {cancel_idx} ({urls[cancel_idx]})')
        result = await _rpc(
            client,
            urls[cancel_idx],
            'CancelTask',
            CancelTaskRequest(id=task_id),
        )
        cancelled = Task()
        ParseDict(result, cancelled)
        print(
            f'            cancel returned state='
            f'{TaskState.Name(cancelled.status.state)}'
        )

        # Stop draining the (now-cancelled) stream. CancelledError is a
        # BaseException, so suppress it explicitly.
        send_call.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await send_call

        # 4) Confirm from replica 0 that the cancel is visible everywhere.
        await asyncio.sleep(1.0)
        final = await _get_task(client, urls[0], task_id)
        print(
            f'[verify]    replica 0 sees final state='
            f'{TaskState.Name(final.status.state)}'
        )
        if final.status.state != _CANCELED:
            raise RuntimeError(
                'expected the task to be CANCELED across all replicas, got '
                f'{TaskState.Name(final.status.state)}'
            )
        print(
            '\nOK: one task was started on replica 0, observed on replica 1, '
            'and cancelled from another replica -- consistently.'
        )


def main() -> None:
    """Parses args and runs the demonstration."""
    parser = argparse.ArgumentParser(description='Exercise an A2A cluster')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument(
        '--ports',
        default='41241,41242,41243',
        help='Comma-separated replica ports',
    )
    args = parser.parse_args()
    ports = [int(p) for p in args.ports.split(',')]
    asyncio.run(run(args.host, ports))


if __name__ == '__main__':
    main()
