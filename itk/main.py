import asyncio  # noqa: I001
import base64
import logging
import os
import signal
import uuid

from collections.abc import AsyncIterator, Callable
from typing import Any

import click
import grpc
import httpx
import uvicorn

from fastapi import FastAPI
from pyproto import instruction_pb2

from a2a.client import Client, ClientConfig, ClientFactory
from a2a.client.errors import A2AClientJSONRPCError
from a2a.grpc import a2a_pb2_grpc
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication, A2ARESTFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler, GrpcHandler
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    TaskUpdater,
    BasePushNotificationSender,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    FilePart,
    FileWithBytes,
    Message,
    Part,
    PushNotificationConfig,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskIdParams,
    TransportProtocol,
)
from a2a.utils import new_agent_text_message


log_level_str = os.environ.get('ITK_LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)


def wrap_instruction_to_request(
    instruction: instruction_pb2.Instruction,
) -> Message:
    """Wraps an Instruction proto in an A2A Message.

    Args:
        instruction: The Instruction protobuf to wrap inside the message.

    Returns:
        Message: An A2A Message containing the serialized instruction as a byte file part.

    """
    inst_bytes = instruction.SerializeToString()
    b64_inst = base64.b64encode(inst_bytes).decode('utf-8')

    return Message(
        role=Role.user,
        message_id=str(uuid.uuid4()),
        parts=[
            Part(
                root=FilePart(
                    file=FileWithBytes(
                        bytes=b64_inst,
                        mime_type='application/x-protobuf',
                        name='instruction.bin',
                    )
                )
            )
        ],
        metadata={'a2a/protocol_version': '0.3'},
    )


def _get_text_from_part(part: Any) -> str | None:
    """Safely extracts text string from a Part object supporting protobuf, pydantic, and raw dict."""
    if not part:
        return None
    if hasattr(part, 'HasField'):
        try:
            if part.HasField('text'):
                return part.text
        except ValueError:
            pass
    root = getattr(part, 'root', part)
    if isinstance(root, dict):
        return root.get('text')
    return getattr(root, 'text', None)


def _extract_text_from_event(event: Any) -> list[str]:
    """Extracts text parts from an event's message in v0.3."""
    text_parts = []
    message = None
    if hasattr(event, 'role') and hasattr(event, 'parts'):  # Likely a Message
        message = event
    elif isinstance(event, tuple):
        for item in event:
            if item is None:
                continue

            if hasattr(item, 'role') and hasattr(item, 'parts'):
                message = item
                break
            status = getattr(item, 'status', None) or getattr(
                getattr(item, 'status_update', None),
                'status',
                None,
            )
            if status and getattr(status, 'message', None):
                message = status.message
                break
            if getattr(item, 'message', None) and hasattr(
                item.message, 'parts'
            ):
                message = item.message
                break

    if message:
        for p in message.parts:
            p_root = getattr(p, 'root', p)
            t = getattr(p_root, 'text', None)
            if t:
                text_parts.append(t)
    return text_parts


async def _handle_call_agent_with_resubscribe(
    client: Client, request: Message
) -> list[str]:
    """Handles the send-disconnect-resubscribe flow in v0.3."""
    results = []
    logger.info('Executing re-subscribe behavior')
    agen = client.send_message(request)
    task_id = None

    async for event in agen:
        logger.info('Event before disconnect: %s', event)
        if isinstance(event, tuple):
            task_id = event[0].id
        elif isinstance(event, Message):
            pass

        break

    await agen.aclose()
    logger.info('Disconnected from task %s. Now re-subscribing.', task_id)

    resub_agen = client.resubscribe(TaskIdParams(id=task_id))

    task_obj = None
    finished = False
    async for event in resub_agen:
        logger.info('Event after re-subscribe: %s', event)
        if (
            isinstance(event, tuple)
            and len(event) > 0
            and hasattr(event[0], 'history')
        ):
            task_obj = event[0]

        if task_obj and hasattr(task_obj, 'history'):
            for msg in task_obj.history:
                if str(msg.role) == '2' or 'agent' in str(msg.role).lower():
                    for part in msg.parts:
                        t = _get_text_from_part(part)
                        if t and 'task-finished' in t:
                            logger.info('Found task-finished in history, breaking loop.')
                            results.append(t.replace('task-finished', ''))
                            finished = True
                            break
                if finished:
                    break
        if finished:
            break

        extracted_text = _extract_text_from_event(event)
        for text in extracted_text:
            processed_text = text.replace('task-finished', '')
            results.append(processed_text)
        logger.info('Filtered text added to results: %s', results)
        if any('task-finished' in t for t in extracted_text):
            logger.info(
                'Received task-finished after re-subscribe, breaking loop.'
            )
            finished = True
            break

    if not results and task_obj and hasattr(task_obj, 'history'):
        logger.info('Results empty after loop, reading from history.')
        for msg in task_obj.history:
            if str(msg.role) == '2' or 'agent' in str(msg.role).lower():
                for part in msg.parts:
                    t = _get_text_from_part(part)
                    if t:
                        results.append(t.replace('task-finished', ''))

    logger.info('Canceling task %s after retrieval.', task_id)
    try:
        await client.cancel_task(TaskIdParams(id=task_id))
        logger.info('Task %s cancelled successfully.', task_id)
    except A2AClientJSONRPCError as e:
        logger.error('Failed to cancel task %s: %s', task_id, str(e))
        raise

    return results


async def get_client_with_transport(
    http_client: httpx.AsyncClient,
    url: str,
    transport: TransportProtocol | str,
    streaming: bool = False,
    push_notification_url: str | None = None,
) -> Any:
    """Resolves the agent card and returns an A2AClient configured with the specified transport.

    Args:
        http_client: An asynchronous HTTPX client used for communication.
        url: The URL pointing to the agent's well-known card endpoint.
        transport: The requested transport protocol (e.g., 'jsonrpc', 'grpc', 'http_json').
        streaming: Whether to use streaming.
        push_notification_url: Optional URL for push notifications.

    Returns:
        Any: An initialized A2A client bound to the specified transport.

    Raises:
        ValueError: If the specified transport is not supported or recognized.

    """
    transport_map = {
        'jsonrpc': TransportProtocol.jsonrpc,
        'http_json': TransportProtocol.http_json,
        'grpc': TransportProtocol.grpc,
    }
    if not isinstance(transport, TransportProtocol):
        transport = transport_map.get(
            transport.lower() if isinstance(transport, str) else transport
        )

    if not transport:
        raise ValueError(f'Unsupported transport: {transport}')

    config = ClientConfig()
    config.httpx_client = http_client
    config.grpc_channel_factory = grpc.aio.insecure_channel
    config.supported_transports = [transport]
    config.use_client_preference = True
    config.streaming = streaming

    if push_notification_url:
        config.push_notification_configs = [
            PushNotificationConfig(
                url=f'{push_notification_url}/notifications',
                token='itk-token',
            )
        ]

    return await ClientFactory.connect(url, client_config=config)


def _should_hold(inst: instruction_pb2.Instruction) -> bool:
    """Recursively checks if any part of the instruction requests holding the task."""
    if inst.HasField('return_response') and inst.return_response.hold_task:
        return True
    if inst.HasField('steps'):
        return any(_should_hold(step) for step in inst.steps.instructions)
    return False


async def handle_instruction(
    instruction: instruction_pb2.Instruction,
    call_agent_func: Callable[[instruction_pb2.CallAgent], AsyncIterator[str]],
) -> list[str]:
    """Processes a single Instruction proto recursively.

    Args:
        instruction: The starting Instruction protobuf.
        call_agent_func: An asynchronous callable that handles executing call_agent steps.

    Returns:
        list[str]: A list of string responses gathered from processing the instruction.

    Raises:
        ValueError: If the instruction type is neither call_agent, return_response, nor steps.

    """
    if instruction.HasField('call_agent'):
        return [p async for p in call_agent_func(instruction.call_agent)]
    if instruction.HasField('return_response'):
        return [instruction.return_response.response]
    if instruction.HasField('steps'):
        results = []
        for step in instruction.steps.instructions:
            results.extend(await handle_instruction(step, call_agent_func))
        return results
    raise ValueError('Unknown instruction type')


async def _call_agent_func(
    call_agent_proto: instruction_pb2.CallAgent,
) -> AsyncIterator[str]:
    logger.info(
        'Calling outbound agent: %s via %s',
        call_agent_proto.agent_card_uri,
        call_agent_proto.transport,
    )

    push_notification_url = None
    if call_agent_proto.HasField('push_notification'):
        url = call_agent_proto.push_notification.url
        if not url:
            raise ValueError('URL not specified in push_notification behavior')
        push_notification_url = url
        logger.info(
            'Push notification URL extracted: %s', push_notification_url
        )

    async with httpx.AsyncClient(timeout=30) as http_client:
        client = await get_client_with_transport(
            http_client,
            call_agent_proto.agent_card_uri,
            call_agent_proto.transport,
            streaming=call_agent_proto.streaming,
            push_notification_url=push_notification_url,
        )
        msg = wrap_instruction_to_request(call_agent_proto.instruction)
        if call_agent_proto.HasField('resubscribe'):
            results = await _handle_call_agent_with_resubscribe(client, msg)
            for result in results:
                yield result
        else:
            async for event in client.send_message(msg):
                logger.info('Event received: %s: %s', type(event), event)

                text_parts = _extract_text_from_event(event)
                if text_parts:
                    yield '\n'.join(text_parts)


class V03AgentExecutor(AgentExecutor):
    """Simplified AgentExecutor for ITK v0.3 logic."""

    def __init__(
        self,
        push_config_store: InMemoryPushNotificationConfigStore | None = None,
    ):
        self._push_config_store = push_config_store

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Implementation of the AgentExecutor interface.

        Args:
            context: The request context containing the incoming message.
            event_queue: The event queue to send responses to.

        """
        task_updater = TaskUpdater(
            event_queue,
            task_id=context.task_id or str(uuid.uuid4()),
            context_id=context.context_id or str(uuid.uuid4()),
        )

        # Explicitly create the task by sending it to the queue
        task = Task(
            id=task_updater.task_id,
            context_id=task_updater.context_id,
            status=TaskStatus(state=TaskState.submitted),
            history=[context.message] if context.message else [],
        )
        async with task_updater._lock:  # noqa: SLF001
            await event_queue.enqueue_event(task)

        await task_updater.start_work()

        instruction = None
        # Extract proto from message parts
        for part in context.message.parts:
            part_root = part.root
            if isinstance(part_root, FilePart) and isinstance(
                part_root.file, FileWithBytes
            ):
                try:
                    raw_bytes = base64.b64decode(part_root.file.bytes)
                    instruction = instruction_pb2.Instruction()
                    instruction.ParseFromString(raw_bytes)
                    break
                except Exception:
                    logger.exception('Failed to parse Instruction proto')
                    continue

        if not instruction:
            logger.error('No valid Instruction found in message parts')
            error_msg = 'Error: No valid Instruction found in request.'
            await task_updater.failed(message=new_agent_text_message(error_msg))
            return

        should_hold_task = _should_hold(instruction)

        try:

            async def call_agent_func_wrapper(
                call_agent_proto: instruction_pb2.CallAgent,
            ) -> AsyncIterator[str]:
                if self._push_config_store and call_agent_proto.HasField(
                    'push_notification'
                ):
                    url = call_agent_proto.push_notification.url
                    if url:
                        await self._push_config_store.set_info(
                            task_updater.task_id,
                            PushNotificationConfig(url=f'{url}/notifications'),
                        )
                        logger.info(
                            'Saved push notification config for current task %s in executor',
                            task_updater.task_id,
                        )

                async for event in _call_agent_func(call_agent_proto):
                    yield event

            result = await handle_instruction(
                instruction, call_agent_func_wrapper
            )
            response_text = '\n'.join(result)
            if should_hold_task:
                logger.info(
                    'Holding task %s as requested', task_updater.task_id
                )
                # Emitted event: response + task-finished
                logger.info(
                    'Emitting response and task-finished for held task %s',
                    task_updater.task_id,
                )
                finnished_msg = new_agent_text_message(
                    response_text + '\ntask-finished'
                )
                await task_updater.update_status(
                    TaskState.working, message=finnished_msg
                )
                # Periodically emit status updates for up to 5 iterations (10 seconds total)
                # to satisfy resubscribing clients, then auto-complete to prevent resource leaks!
                try:
                    for _ in range(5):
                        logger.info(
                            'Emitting periodic status update for held task %s',
                            task_updater.task_id,
                        )
                        await task_updater.update_status(
                            TaskState.working, message=finnished_msg
                        )
                        await asyncio.sleep(2)
                    
                    logger.info('Held task %s timed out, auto-completing', task_updater.task_id)
                except asyncio.CancelledError:
                    logger.info(
                        'Task %s cancelled, closing event queue.',
                        task_updater.task_id,
                    )
                    await event_queue.close()
                    return

            response_msg = new_agent_text_message('\n'.join(result))
            await task_updater.complete(message=response_msg)
        except Exception:
            logger.exception('Instruction execution failed')
            await task_updater.failed(message=None)

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Implementation of cancel request method required by AgentExecutor interface."""
        logger.info('Cancel requested for task %s', context.task_id)
        task_updater = TaskUpdater(
            event_queue,
            task_id=context.task_id,
            context_id=context.context_id,
        )
        await task_updater.update_status(TaskState.canceled)


async def create_grpc_server(
    agent_card: AgentCard,
    request_handler: DefaultRequestHandler,
    host: str,
    port: int,
) -> grpc.aio.Server:
    """Creates and configures the gRPC server.

    Args:
        agent_card: The AgentCard specifying the server's identity and capabilities.
        request_handler: The request handler used for routing and handling instructions.
        host: The host address to bind the server to (e.g., '127.0.0.1').
        port: The port to bind the gRPC server.

    Returns:
        grpc.aio.Server: An initialized gRPC Server object.

    """
    server = grpc.aio.server()
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(
        GrpcHandler(agent_card, request_handler), server
    )
    server.add_insecure_port(f'{host}:{port}')
    return server


def create_http_server(
    agent_card: AgentCard,
    request_handler: DefaultRequestHandler,
    host: str,
    port: int,
) -> uvicorn.Server:
    """Creates and configures the HTTP server for JSON-RPC and REST via FastAPI.

    Args:
        agent_card: The AgentCard specifying the server's identity and capabilities.
        request_handler: The request handler used for routing and handling instructions.
        host: The host address to bind the server to (e.g., '127.0.0.1').
        port: The port to bind the HTTP server.

    Returns:
        uvicorn.Server: An initialized Uvicorn Server instance holding the FastAPI app.

    """
    app = FastAPI(title='ITK v03 Agent Server (Consolidated)')

    @app.get('/.well-known/agent-card.json')
    def get_agent_card():
        return agent_card

    app.mount(
        '/jsonrpc', A2AFastAPIApplication(agent_card, request_handler).build()
    )
    app.mount(
        '/rest', A2ARESTFastAPIApplication(agent_card, request_handler).build()
    )
    return uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_config=None)
    )


async def _run_agent(http_port: int, grpc_port: int) -> None:
    host = '127.0.0.1'

    skill = AgentSkill(
        id='itk_v03_proto_skill',
        name='ITK v03 Proto Skill',
        description='Handles raw byte Instruction protos in v03 subproject.',
        tags=['proto', 'v03', 'itk'],
        examples=['Roll a dice', 'Call another agent'],
    )

    agent_card = AgentCard(
        name='ITK v03 Agent',
        description='Multi-transport agent supporting raw Instruction protos (Consolidated).',
        url=f'http://{host}:{http_port}/jsonrpc/',
        version='0.3.0',
        protocol_version='0.3.0',
        default_input_modes=['text'],
        default_output_modes=['text'],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
        preferred_transport=TransportProtocol.jsonrpc,
        additional_interfaces=[
            AgentInterface(
                url=f'http://{host}:{http_port}/rest/',
                transport=TransportProtocol.http_json,
            ),
            AgentInterface(
                url=f'{host}:{grpc_port}',
                transport=TransportProtocol.grpc,
            ),
        ],
    )

    httpx_client = httpx.AsyncClient()
    push_config_store = InMemoryPushNotificationConfigStore()
    push_sender = BasePushNotificationSender(httpx_client, push_config_store)
    request_handler = DefaultRequestHandler(
        agent_executor=V03AgentExecutor(push_config_store=push_config_store),
        task_store=InMemoryTaskStore(),
        push_config_store=push_config_store,
        push_sender=push_sender,
    )

    http_server = create_http_server(
        agent_card, request_handler, host, http_port
    )
    grpc_server = await create_grpc_server(
        agent_card, request_handler, host, grpc_port
    )

    # Signal handling
    loop = asyncio.get_running_loop()

    async def shutdown() -> None:
        logger.info('Shutting down...')
        http_server.should_exit = True
        await grpc_server.stop(5)
        await httpx_client.aclose()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    await grpc_server.start()
    await http_server.serve()


@click.command()
@click.option('--httpPort', 'http_port', default=10101)
@click.option('--grpcPort', 'grpc_port', default=11001)
def main(http_port: int, grpc_port: int) -> None:
    """Command line entry point for starting the ITK v03 merged Agent.

    Args:
        http_port: The HTTP port to listen on for REST/JSON-RPC calls.
        grpc_port: The gRPC port to listen on.

    """
    asyncio.run(_run_agent(http_port, grpc_port))


if __name__ == '__main__':
    main()
