import asyncio, json, sys

# Reads one request and exits immediately without sending a response.


async def reader():
    loop = asyncio.get_event_loop()
    # read single line then exit silently
    await loop.run_in_executor(None, sys.stdin.readline)
    # terminate process without output
    sys.exit(0)


if __name__ == '__main__':
    asyncio.run(reader())
