"""Shared wiring for the multi-replica (cluster mode) sample.

Every replica in this sample builds its handler from the SAME durable task store
and the SAME event stream (pointed at one shared database). That is what lets any
replica serve any request for any task - send, resubscribe, and cancel all work
regardless of which replica the load balancer picks.

By default this uses a file-backed SQLite database so the sample runs with no
external services. Set A2A_CLUSTER_DSN to a Postgres/MySQL async DSN for a more
realistic setup, e.g.:

    export A2A_CLUSTER_DSN='postgresql+asyncpg://user:pass@localhost/a2a'
"""

import asyncio
import logging
import os
import tempfile

from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.cluster import VersionedDatabaseTaskStore
from a2a.server.cluster.database_event_stream import DatabaseTaskEventStream
from a2a.server.events.event_queue import EventQueue
from a2a.server.models import Base
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    TaskState,
)


logger = logging.getLogger(__name__)


def default_sqlite_path() -> str:
    """Path to the shared SQLite file used when no DSN is configured."""
    return os.environ.get(
        'A2A_CLUSTER_SQLITE',
        str(Path(tempfile.gettempdir()) / 'a2a_cluster_demo.db'),
    )


def default_dsn() -> str:
    """The shared-database DSN. SQLite file by default; override via env."""
    dsn = os.environ.get('A2A_CLUSTER_DSN')
    if dsn:
        return dsn
    # A file (not :memory:) so separate replica processes share one database.
    return f'sqlite+aiosqlite:///{default_sqlite_path()}'


async def init_schema(dsn: str) -> None:
    """Creates the shared tables (task + task_events) once, up front."""
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def build_cluster_backends(
    dsn: str,
) -> tuple[VersionedDatabaseTaskStore, DatabaseTaskEventStream]:
    """Builds a versioned store + event stream over the shared database.

    Each replica calls this with the same DSN. `create_table=False` because
    `init_schema` owns table creation.
    """
    store_engine = create_async_engine(dsn)
    stream_engine = create_async_engine(dsn)
    store = VersionedDatabaseTaskStore(engine=store_engine, create_table=False)
    stream = DatabaseTaskEventStream(
        engine=stream_engine, create_table=False, poll_interval_s=0.2
    )
    return store, stream


def build_agent_card(base_url: str) -> AgentCard:
    """The agent card advertised by every replica (same logical agent)."""
    return AgentCard(
        name='Cluster Demo Agent',
        description='A slow agent used to demonstrate multi-replica A2A.',
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=True, push_notifications=False
        ),
        default_input_modes=['text'],
        default_output_modes=['text', 'task-status'],
        skills=[
            AgentSkill(
                id='cluster_demo',
                name='Cluster Demo',
                description='Echoes slowly so you can observe it across replicas.',
                tags=['sample', 'cluster'],
                examples=['hello'],
                input_modes=['text'],
                output_modes=['text', 'task-status'],
            )
        ],
        supported_interfaces=[
            AgentInterface(
                protocol_binding='JSONRPC',
                protocol_version='1.0',
                url=f'{base_url}/a2a/jsonrpc',
            ),
        ],
    )


class SlowEchoAgent(AgentExecutor):
    """Goes WORKING, emits ticks slowly, then completes.

    The deliberate slowness makes the multi-replica behaviour observable: you
    can subscribe or cancel from a different replica while a task is mid-flight.
    Resumability across replicas relies on this agent reading state only from
    the task (it does not keep anything in process memory between turns).
    """

    def __init__(self, replica_id: str, ticks: int = 10) -> None:
        self._replica_id = replica_id
        self._ticks = ticks

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Runs the slow echo: WORKING, ticks, artifact, complete."""
        updater = TaskUpdater(
            event_queue,
            str(context.task_id or ''),
            str(context.context_id or ''),
        )
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
        await updater.start_work(
            message=updater.new_agent_message(
                [Part(text=f'[{self._replica_id}] starting')]
            )
        )
        for i in range(self._ticks):
            await asyncio.sleep(1.0)
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message(
                    [Part(text=f'[{self._replica_id}] tick {i + 1}')]
                ),
            )
        await updater.add_artifact(
            [Part(text=f'[{self._replica_id}] done')],
            name='response',
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """No-op: the stop happens via the store CAS when CANCELED is recorded."""
        return
