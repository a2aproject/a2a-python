"""A single cluster-mode replica: one FastAPI app on one port.

All replicas share the same database (via A2A_CLUSTER_DSN or the default SQLite
file), so they form one logical A2A service. Run several of these on different
ports and put any round-robin in front of them.
"""

import argparse
import asyncio
import contextlib
import logging

import uvicorn

from fastapi import FastAPI

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from samples.clustermode.cluster_common import (
    SlowEchoAgent,
    build_agent_card,
    build_cluster_backends,
    default_dsn,
    init_schema,
)


logger = logging.getLogger(__name__)


async def serve(host: str, port: int, replica_id: str, ticks: int) -> None:
    """Runs one replica bound to a shared store + event stream."""
    dsn = default_dsn()
    await init_schema(dsn)

    base_url = f'http://{host}:{port}'
    agent_card = build_agent_card(base_url)
    store, stream = build_cluster_backends(dsn)
    await store.initialize()
    await stream.initialize()

    handler = DefaultRequestHandler(
        agent_executor=SlowEchoAgent(replica_id, ticks=ticks),
        task_store=store,
        agent_card=agent_card,
        event_stream=stream,
    )

    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=handler,
        rpc_url='/a2a/jsonrpc',
    )
    agent_card_routes = create_agent_card_routes(agent_card=agent_card)

    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=agent_card_routes,
        jsonrpc_routes=jsonrpc_routes,
    )

    logger.info(
        'Replica %s listening on %s (dsn=%s)', replica_id, base_url, dsn
    )
    config = uvicorn.Config(app, host=host, port=port, log_level='warning')
    await uvicorn.Server(config).serve()


def main() -> None:
    """Parses args and runs one replica."""
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description='A2A cluster-mode replica')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=41241)
    parser.add_argument('--replica-id', default='replica-0')
    parser.add_argument('--ticks', type=int, default=10)
    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(args.host, args.port, args.replica_id, args.ticks))


if __name__ == '__main__':
    main()
