import argparse
import asyncio

import grpc
import httpx

from a2a.client import A2ACardResolver, ClientConfig, create_text_client


async def main() -> None:
    """Run the simple A2A terminal client using TextClient."""
    parser = argparse.ArgumentParser(description='A2A Simple Text Client')
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
    if args.transport == 'GRPC':
        config.grpc_channel_factory = grpc.aio.insecure_channel

    print(
        f'Connecting to {args.url} (preferred transport: {args.transport or "Any"})'
    )

    async with httpx.AsyncClient() as httpx_client:
        resolver = A2ACardResolver(httpx_client, args.url)
        card = await resolver.get_agent_card()
        print('\n✓ Agent Card Found:')
        print(f'  Name: {card.name}')

    text_client = await create_text_client(card, client_config=config)

    actual_transport = getattr(
        text_client.client, '_transport', text_client.client
    )
    print(f'  Picked Transport: {actual_transport.__class__.__name__}')

    print('\nConnected! Send a message or type /quit to exit.')

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

        try:
            response = await text_client.send_text_message(user_input)
            print(f'Agent: {response}')
        except (httpx.RequestError, grpc.RpcError) as e:
            print(f'Error communicating with agent: {e}')

    await text_client.close()


if __name__ == '__main__':
    asyncio.run(main())
