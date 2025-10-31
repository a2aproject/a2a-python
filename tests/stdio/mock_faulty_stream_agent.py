import asyncio, json, sys, uuid

# Emits one good event, one malformed line, another good event, then eos.


async def handle_stream(request_id):
    def good(i):
        return {
            'id': request_id,
            'jsonrpc': '2.0',
            'result': {
                'kind': 'message',
                'message_id': str(uuid.uuid4()),
                'parts': [{'kind': 'text', 'text': f'good {i}'}],
                'role': 'agent',
            },
        }

    # first good
    print(json.dumps(good(0)), flush=True)
    await asyncio.sleep(0.01)
    # malformed line
    print('{"this_is": "not valid json"', flush=True)  # missing closing brace
    await asyncio.sleep(0.01)
    # second good
    print(json.dumps(good(1)), flush=True)
    await asyncio.sleep(0.01)
    # eos
    print(
        json.dumps({'id': request_id, 'jsonrpc': '2.0', 'eos': True}),
        flush=True,
    )


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
        if req.get('method') == 'message/stream':
            await handle_stream(req.get('id'))


if __name__ == '__main__':
    asyncio.run(reader())
