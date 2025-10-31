import asyncio, json, sys, uuid

# Simple mock stdio agent: reads line-delimited JSON requests, writes responses.
# Supports methods: message/send, message/stream (streaming two events), tasks/resubscribe.
# Emits minimal 'result' objects shaped like Message or Task depending on request.


async def handle_send(params):
    # Return a mock Message
    return {
        'kind': 'message',
        'message_id': str(uuid.uuid4()),
        'parts': [
            {
                'kind': 'text',
                'text': f'echo: {params["message"]["parts"][0]["text"]}',
            }
        ],
        'role': 'agent',
    }


async def handle_stream(params, request_id):
    # Emit two streaming events then end marker
    for i in range(2):
        event = {
            'id': request_id,
            'jsonrpc': '2.0',
            'result': {
                'kind': 'message',
                'message_id': str(uuid.uuid4()),
                'parts': [{'kind': 'text', 'text': f'stream chunk {i}'}],
                'role': 'agent',
            },
        }
        print(json.dumps(event), flush=True)
        await asyncio.sleep(0.01)
    # explicit end-of-stream marker
    eos = {'id': request_id, 'jsonrpc': '2.0', 'eos': True}
    print(json.dumps(eos), flush=True)


async def reader():
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get('method')
        if method == 'message/send':
            result = await handle_send(req.get('params', {}))
            response = {'id': req.get('id'), 'jsonrpc': '2.0', 'result': result}
            print(json.dumps(response), flush=True)
        elif method == 'message/stream':
            await handle_stream(req.get('params', {}), req.get('id'))
        elif method == 'tasks/resubscribe':
            # Emit one event
            event = {
                'id': req.get('id'),
                'jsonrpc': '2.0',
                'result': {
                    'kind': 'message',
                    'message_id': str(uuid.uuid4()),
                    'parts': [{'kind': 'text', 'text': 'resubscribe event'}],
                    'role': 'agent',
                },
            }
            print(json.dumps(event), flush=True)
            # end-of-stream marker for resubscribe
            eos = {'id': req.get('id'), 'jsonrpc': '2.0', 'eos': True}
            print(json.dumps(eos), flush=True)
        else:
            # Unknown method: ignore
            continue


if __name__ == '__main__':
    asyncio.run(reader())
