import asyncio
import json
import logging
import sys
import traceback

import click
import httpx

from dotenv import load_dotenv


load_dotenv()
logging.basicConfig()


async def fetch_api_catalog(base_url: str):
    url = f'{base_url.rstrip("/")}/.well-known/api-catalog'
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


@click.command()
@click.option('--host', 'host', default='localhost')
@click.option('--port', 'port', default=9999)
def main(host: str, port: int):
    base = f'http://{host}:{port}'
    catalog = asyncio.run(fetch_api_catalog(base))
    print(json.dumps(catalog))


if __name__ == '__main__':
    try:
        main()
    except Exception as _:
        print(traceback.format_exc(), file=sys.stderr)
