import logging
import sys
import traceback

import click
import uvicorn

from agent_executors import (  # type: ignore[import-untyped]
    EchoAgentExecutor,
    HelloWorldAgentExecutor,
)
from dotenv import load_dotenv

from a2a.server.apps import StarletteBuilder, StarletteRouteBuilder
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill


load_dotenv()
logging.basicConfig()


@click.command()
@click.option('--host', 'host', default='localhost')
@click.option('--port', 'port', default=9999)
def main(host: str, port: int) -> None:
    """Start the API catalog server with the given host and port."""
    hello_skill = AgentSkill(
        id='hello_world',
        name='Returns hello world',
        description='just returns hello world',
        tags=['hello world'],
        examples=['hi', 'hello world'],
    )
    hello_card = AgentCard(
        name='Hello World Agent',
        description='Just a hello world agent',
        url=f'http://{host}:{port}/a2a/hello',
        version='1.0.0',
        defaultInputModes=['text'],
        defaultOutputModes=['text'],
        capabilities=AgentCapabilities(streaming=True),
        skills=[hello_skill],
        supportsAuthenticatedExtendedCard=False,
    )
    hello_handler = DefaultRequestHandler(
        agent_executor=HelloWorldAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    hello_agent = StarletteRouteBuilder(
        agent_card=hello_card,
        http_handler=hello_handler,
    )

    echo_skill = AgentSkill(
        id='echo',
        name='Echo input',
        description='Returns the input text as is',
        tags=['echo'],
        examples=['Hello!', 'Repeat after me'],
    )
    echo_card = AgentCard(
        name='Echo Agent',
        description='An agent that echoes back your input.',
        url=f'http://{host}:{port}/a2a/echo',
        version='1.0.0',
        defaultInputModes=['text'],
        defaultOutputModes=['text'],
        capabilities=AgentCapabilities(streaming=True),
        skills=[echo_skill],
        supportsAuthenticatedExtendedCard=False,
    )
    echo_handler = DefaultRequestHandler(
        agent_executor=EchoAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    echo_agent = StarletteRouteBuilder(
        agent_card=echo_card,
        http_handler=echo_handler,
    )

    server = StarletteBuilder().mount(hello_agent).mount(echo_agent).build()
    uvicorn.run(server, host=host, port=port)


if __name__ == '__main__':
    try:
        main()
    except Exception as _:
        print(traceback.format_exc(), file=sys.stderr)
