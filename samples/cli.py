import argparse
import asyncio
import os
import signal
import uuid

from typing import Any

import grpc
import httpx

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_artifact_text, get_message_text
from a2a.helpers.agent_card import display_agent_card
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState


async def _handle_stream(
    stream: Any, current_task_id: str | None
) -> str | None:
    async for event in stream:
        if event.HasField('message'):
            print('Message:', get_message_text(event.message, delimiter=' '))
            return None

        if not current_task_id:
            if event.HasField('task'):
                current_task_id = event.task.id
                print('--- Task Started ---')
                print(f'Task [state={TaskState.Name(event.task.status.state)}]')
            else:
                raise ValueError(f'Unexpected first event: {event}')

        if event.HasField('status_update'):
            state_name = TaskState.Name(event.status_update.status.state)
            message_text = (
                ': '
                + get_message_text(
                    event.status_update.status.message, delimiter=' '
                )
                if event.status_update.status.HasField('message')
                else ''
            )
            print(f'TaskStatusUpdate [state={state_name}]{message_text}')
            if state_name in (
                'TASK_STATE_COMPLETED',
                'TASK_STATE_FAILED',
                'TASK_STATE_CANCELED',
                'TASK_STATE_REJECTED',
            ):
                current_task_id = None
                print('--- Task Finished ---')
        elif event.HasField('artifact_update'):
            print(
                f'TaskArtifactUpdate [name={event.artifact_update.artifact.name}]:',
                get_artifact_text(
                    event.artifact_update.artifact, delimiter=' '
                ),
            )
    return current_task_id


async def main() -> None:
    """Run the A2A terminal client."""
    parser = argparse.ArgumentParser(description='A2A Terminal Client')
    parser.add_argument(
        '--url', default='http://127.0.0.1:41241', help='Agent base URL'
    )
    parser.add_argument(
        '--transport',
        default=None,
        help='Preferred transport (JSONRPC, HTTP+JSON, GRPC)',
    )
    args = parser.parse_args()

    config = ClientConfig(
        grpc_channel_factory=grpc.aio.insecure_channel,
    )
    if args.transport:
        config.supported_protocol_bindings = [args.transport]

    print(
        f'Connecting to {args.url} (preferred transport: {args.transport or "Any"})'
    )

    async with httpx.AsyncClient() as httpx_client:
        resolver = A2ACardResolver(httpx_client, args.url)
        card = await resolver.get_agent_card()
        print('\n✓ Agent Card Found:')
        display_agent_card(card)

    client = await create_client(card, client_config=config)

    actual_transport = getattr(client, '_transport', client)
    print(f'  Picked Transport: {actual_transport.__class__.__name__}')

    print('\nConnected! Send a message or type /quit to exit.')

    current_task_id = None
    current_context_id = str(uuid.uuid4())

    while True:
        try:
            loop = asyncio.get_running_loop()
            user_input = await loop.run_in_executor(None, input, 'You: ')
        except KeyboardInterrupt:
            break

        if user_input.lower() in ('/quit', '/exit'):
            break
        if not user_input.strip():
            continue

        message = Message(
            role=Role.ROLE_USER,
            message_id=str(uuid.uuid4()),
            parts=[Part(text=user_input)],
            task_id=current_task_id,
            context_id=current_context_id,
        )

        request = SendMessageRequest(message=message)

        try:
            stream = client.send_message(request)
            current_task_id = await _handle_stream(stream, current_task_id)
        except (httpx.RequestError, grpc.RpcError) as e:
            print(f'Error communicating with agent: {e}')

    await client.close()


if __name__ == '__main__':
    signal.signal(signal.SIGINT, lambda sig, frame: os._exit(0))
    asyncio.run(main())
