import argparse
import asyncio
import contextlib
import uuid

import httpx

from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState


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

    config = ClientConfig()
    if args.transport:
        config.supported_protocol_bindings = [args.transport]

    print(
        f'Connecting to {args.url} (preferred transport: {args.transport or "Any"})'
    )

    async with httpx.AsyncClient() as httpx_client:
        resolver = A2ACardResolver(httpx_client, args.url)
        card = await resolver.get_agent_card()
        print('\n✓ Agent Card Found:')
        print(f'  Name: {card.name}')

    client = await ClientFactory.connect(card, client_config=config)

    actual_transport = getattr(client, '_transport', client)
    print(f'  Picked Transport: {actual_transport.__class__.__name__}')

    print('\nConnected! Send a message or type /quit to exit.')

    current_task_id = None
    current_context_id = str(uuid.uuid4())

    while True:
        try:
            user_input = input('You: ')
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
            async for event, task in stream:
                if not task:
                    continue
                if not current_task_id:
                    current_task_id = task.id

                if event:
                    if event.HasField('status_update'):
                        state_name = TaskState.Name(
                            event.status_update.status.state
                        )
                        print(f'TaskStatusUpdate [{state_name}]:', end=' ')
                        if event.status_update.status.HasField('message'):
                            for (
                                part
                            ) in event.status_update.status.message.parts:
                                if part.text:
                                    print(part.text, end=' ')
                        print()

                        if (
                            event.status_update.status.state
                            == TaskState.TASK_STATE_COMPLETED
                        ):
                            current_task_id = None
                            print('--- Task Completed ---')

                    elif event.HasField('artifact_update'):
                        print(
                            f'TaskArtifactUpdate [{event.artifact_update.artifact.name}]:',
                            end=' ',
                        )
                        for part in event.artifact_update.artifact.parts:
                            if part.text:
                                print(part.text, end=' ')
                        print()

        except Exception as e:
            print(f'Error communicating with agent: {e}')

    await client.close()


if __name__ == '__main__':
    with contextlib.suppress(KeyboardInterrupt, asyncio.CancelledError):
        asyncio.run(main())
