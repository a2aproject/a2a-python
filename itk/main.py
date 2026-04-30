import argparse  # noqa: I001
import asyncio
import base64
import logging
import os
import signal
import uuid

import grpc
import httpx
import uvicorn

from fastapi import FastAPI
from typing import Any

from pyproto import instruction_pb2

from a2a.client import Client, ClientConfig, create_client
from a2a.client.errors import A2AClientError
from a2a.compat.v0_3 import a2a_v0_3_pb2_grpc
from a2a.compat.v0_3.grpc_handler import CompatGrpcHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers import DefaultRequestHandler, GrpcHandler
from a2a.server.tasks import (
    TaskUpdater,
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.context import ServerCallContext
from a2a.types import a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    CancelTaskRequest,
    Message,
    Part,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskPushNotificationConfig,
)
from a2a.utils import TransportProtocol
from a2a.utils.errors import TaskNotCancelableError
from a2a.server.tasks.push_notification_sender import PushNotificationEvent

log_level_str = os.environ.get('ITK_LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)


def extract_instruction(
    message: Message | None,
) -> instruction_pb2.Instruction | None:
    """Extracts an Instruction proto from an A2A Message."""
    if not message or not message.parts:
        return None

    for part in message.parts:
        # 1. Handle binary protobuf part (media_type or filename)
        if (
            part.media_type == 'application/x-protobuf'
            or part.filename == 'instruction.bin'
        ):
            try:
                inst = instruction_pb2.Instruction()
                if part.raw:
                    inst.ParseFromString(part.raw)
                elif part.text:
                    # Some clients might send it as base64 in text part
                    raw = base64.b64decode(part.text)
                    inst.ParseFromString(raw)
            except Exception:  # noqa: BLE001
                logger.debug(
                    'Failed to parse instruction from binary part',
                    exc_info=True,
                )
                continue
            else:
                return inst

        # 2. Handle base64 encoded instruction in any text part
        if part.text:
            try:
                raw = base64.b64decode(part.text)
                inst = instruction_pb2.Instruction()
                inst.ParseFromString(raw)
            except Exception:  # noqa: BLE001
                logger.debug(
                    'Failed to parse instruction from text part', exc_info=True
                )
                continue
            else:
                return inst
    return None


def _extract_text_from_event(event: Any) -> list[str]:
    """Extracts text parts from an event's message."""
    if isinstance(event, tuple):
        results = []
        for item in event:
            results.extend(_extract_text_from_event(item))
        return results

    message = None
    if hasattr(event, 'HasField'):
        if event.HasField('message'):
            message = event.message
        elif event.HasField('task') and event.task.status.HasField('message'):
            message = event.task.status.message
        elif event.HasField(
            'status_update'
        ) and event.status_update.status.HasField('message'):
            message = event.status_update.status.message

    results = []
    if message:
        results.extend(part.text for part in message.parts if part.text)
    return results


async def _handle_call_agent_with_resubscribe(
    client: Client, request: SendMessageRequest
) -> list[str]:
    """Handles the send-disconnect-resubscribe flow."""
    results = []
    logger.info('Executing re-subscribe behavior')
    agen = client.send_message(request)
    task_id = None

    async for event in agen:
        logger.info('Event before disconnect: %s', event)
        if event.HasField('task'):
            task_id = event.task.id
        elif event.HasField('status_update'):
            task_id = event.status_update.task_id
        break

    await agen.aclose()
    logger.info('Disconnected from task %s. Now re-subscribing.', task_id)

    resub_agen = client.subscribe(SubscribeToTaskRequest(id=task_id))

    task_obj = None
    finished = False
    async for event in resub_agen:
        logger.info('Event after re-subscribe: %s', event)
        if hasattr(event, 'task'):
            task_obj = event.task
        elif hasattr(event, 'HasField') and event.HasField('task'):
            task_obj = event.task

        extracted_text = _extract_text_from_event(event)
        for text in extracted_text:
            processed_text = text.replace('task-finished', '')
            results.append(processed_text)
        if any('task-finished' in text for text in extracted_text):
            logger.info(
                'Received task-finished after re-subscribe, breaking loop.'
            )
            finished = True
            break

    if not results and task_obj and hasattr(task_obj, 'history'):
        logger.info('Results empty after loop, reading from history.')
        for msg in task_obj.history:
            if msg.role == 'ROLE_AGENT' or msg.role == 'agent':
                for part in msg.parts:
                    if part.text:
                        results.append(part.text.replace('task-finished', ''))

    if not finished:
        logger.info('Canceling task %s after retrieval.', task_id)
        try:
            await client.cancel_task(CancelTaskRequest(id=task_id))
            logger.info('Task cancelled successfully: %s', task_id)
        except A2AClientError as e:
            logger.error('Failed to cancel task %s: %s', task_id, str(e))
            raise

    return results


def wrap_instruction_to_request(inst: instruction_pb2.Instruction) -> Message:
    """Wraps an Instruction proto into an A2A Message."""
    inst_bytes = inst.SerializeToString()
    return Message(
        role='ROLE_USER',
        message_id=str(uuid.uuid4()),
        parts=[
            Part(
                raw=inst_bytes,
                media_type='application/x-protobuf',
                filename='instruction.bin',
            )
        ],
    )


async def handle_call_agent(
    call: instruction_pb2.CallAgent,
) -> list[str]:
    """Handles the CallAgent instruction by invoking another agent."""
    logger.info('Calling agent %s via %s', call.agent_card_uri, call.transport)

    # Mapping transport string to TransportProtocol enum
    transport_map = {
        'JSONRPC': TransportProtocol.JSONRPC,
        'HTTP+JSON': TransportProtocol.HTTP_JSON,
        'HTTP_JSON': TransportProtocol.HTTP_JSON,
        'REST': TransportProtocol.HTTP_JSON,
        'GRPC': TransportProtocol.GRPC,
    }

    selected_transport = transport_map.get(
        call.transport.upper(), TransportProtocol.JSONRPC
    )
    if selected_transport is None:
        raise ValueError(f'Unsupported transport: {call.transport}')

    config = ClientConfig()
    config.grpc_channel_factory = grpc.aio.insecure_channel
    config.supported_protocol_bindings = [selected_transport]
    config.streaming = call.streaming or (
        selected_transport == TransportProtocol.GRPC
    )

    if call.HasField('resubscribe') and not config.streaming:
        raise ValueError('Re-subscription requires streaming to be enabled')

    if call.HasField('push_notification'):
        url = call.push_notification.url
        if not url:
            raise ValueError('URL not specified in push_notification behavior')
        if not url.startswith(('http://', 'https://')):
            url = f'http://{url}'
        config.push_notification_config = TaskPushNotificationConfig(
            url=f'{url}/notifications',
            token='itk-token',
        )

    async with httpx.AsyncClient(timeout=30.0) as httpx_client:
        config.httpx_client = httpx_client
        try:
            client = await create_client(
                call.agent_card_uri,
                client_config=config,
            )

            # Wrap nested instruction
            nested_msg = wrap_instruction_to_request(call.instruction)
            request = SendMessageRequest(message=nested_msg)

            results = []

            if call.HasField('resubscribe'):
                results.extend(
                    await _handle_call_agent_with_resubscribe(client, request)
                )
            else:
                async for event in client.send_message(request):
                    logger.info('Event: %s', event)
                    results.extend(_extract_text_from_event(event))

        except Exception as e:
            logger.exception('Failed to call outbound agent')
            raise RuntimeError(
                f'Outbound call to {call.agent_card_uri} failed: {e!s}'
            ) from e
        else:
            return results


def _should_hold(inst: instruction_pb2.Instruction) -> bool:
    """Recursively checks if any part of the instruction requests holding the task."""
    if inst.HasField('return_response') and inst.return_response.hold_task:
        return True
    if inst.HasField('steps'):
        return any(_should_hold(step) for step in inst.steps.instructions)
    return False


async def handle_instruction(
    inst: instruction_pb2.Instruction,
) -> list[str]:
    """Recursively handles instructions."""
    if inst.HasField('call_agent'):
        return await handle_call_agent(inst.call_agent)
    if inst.HasField('return_response'):
        return [inst.return_response.response]
    if inst.HasField('steps'):
        all_results = []
        for step in inst.steps.instructions:
            results = await handle_instruction(step)
            all_results.extend(results)
        return all_results
    raise ValueError('Unknown instruction type')


class V10AgentExecutor(AgentExecutor):
    """Executor for ITK v10 agent tasks."""

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Executes a task instruction."""
        logger.info('Executing task %s', context.task_id)
        task_updater = TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        )

        # Explicitly create the task by sending it to the queue
        task = Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            history=[context.message] if context.message else [],
        )
        async with task_updater._lock:  # noqa: SLF001
            await event_queue.enqueue_event(task)

        await task_updater.update_status(TaskState.TASK_STATE_WORKING)

        instruction = extract_instruction(context.message)
        if not instruction:
            error_msg = 'No valid instruction found in request'
            logger.error(error_msg)
            await task_updater.update_status(
                TaskState.TASK_STATE_FAILED,
                message=task_updater.new_agent_message([Part(text=error_msg)]),
            )
            return

        should_hold_task = _should_hold(instruction)

        try:
            logger.info('Instruction: %s', instruction)
            results = await handle_instruction(instruction)

            response_text = '\n'.join(results)
            logger.info('Response: %s', response_text)

            if should_hold_task:
                logger.info('Holding task %s as requested', context.task_id)
                # Emitted event: response + task-finished
                logger.info(
                    'Emitting response and task-finished for held task %s', context.task_id
                )
                await task_updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=task_updater.new_agent_message(
                        [Part(text=response_text + '\n' +'task-finished')]
                    ),
                )
                await asyncio.sleep(2)

                # Continue emitting "task-finished" every 2 seconds
                try:
                    while True:
                        logger.info(
                            'Emitting periodic status update for held task %s',
                            context.task_id,
                        )
                        await task_updater.update_status(
                            TaskState.TASK_STATE_WORKING,
                            message=None,
                        )
                        await asyncio.sleep(2)
                except asyncio.CancelledError:
                    logger.info('Task %s cancelled', context.task_id)
                    return
            else:
                await task_updater.update_status(
                    TaskState.TASK_STATE_COMPLETED,
                    message=task_updater.new_agent_message(
                        [Part(text=response_text)]
                    ),
                )
                logger.info('Task %s completed', context.task_id)
        except Exception as e:
            logger.exception('Error during instruction handling')
            await task_updater.update_status(
                TaskState.TASK_STATE_FAILED,
                message=None,
            )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Cancels a task."""
        logger.info('Cancel requested for task %s', context.task_id)
        task_updater = TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        )
        await task_updater.update_status(TaskState.TASK_STATE_CANCELED)


async def main_async(http_port: int, grpc_port: int) -> None:
    """Starts the Agent with HTTP and gRPC interfaces."""
    interfaces = [
        AgentInterface(
            protocol_binding=TransportProtocol.GRPC,
            url=f'127.0.0.1:{grpc_port}',
            protocol_version='1.0',
        ),
        AgentInterface(
            protocol_binding=TransportProtocol.GRPC,
            url=f'127.0.0.1:{grpc_port}',
            protocol_version='0.3',
        ),
    ]

    interfaces.append(
        AgentInterface(
            protocol_binding=TransportProtocol.JSONRPC,
            url=f'http://127.0.0.1:{http_port}/jsonrpc/',
            protocol_version='1.0',
        )
    )
    interfaces.append(
        AgentInterface(
            protocol_binding=TransportProtocol.JSONRPC,
            url=f'http://127.0.0.1:{http_port}/jsonrpc/',
            protocol_version='0.3',
        )
    )
    interfaces.append(
        AgentInterface(
            protocol_binding=TransportProtocol.HTTP_JSON,
            url=f'http://127.0.0.1:{http_port}/rest/',
            protocol_version='1.0',
        )
    )
    interfaces.append(
        AgentInterface(
            protocol_binding=TransportProtocol.HTTP_JSON,
            url=f'http://127.0.0.1:{http_port}/rest/',
            protocol_version='0.3',
        )
    )

    agent_card = AgentCard(
        name='ITK v10 Agent',
        description='Python agent using SDK 1.0.',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=interfaces,
    )

    task_store = InMemoryTaskStore()
    push_config_store = InMemoryPushNotificationConfigStore()
    httpx_client = httpx.AsyncClient()
    push_sender = BasePushNotificationSender(
        httpx_client=httpx_client,
        config_store=push_config_store,
        context=ServerCallContext(),
    )

    handler = DefaultRequestHandler(
        agent_executor=V10AgentExecutor(),
        agent_card=agent_card,
        task_store=task_store,
        queue_manager=InMemoryQueueManager(),
        push_config_store=push_config_store,
        push_sender=push_sender,
    )

    handler_extended = DefaultRequestHandler(
        agent_executor=V10AgentExecutor(),
        agent_card=agent_card,
        task_store=task_store,
        queue_manager=InMemoryQueueManager(),
        push_config_store=push_config_store,
        push_sender=push_sender,
        extended_agent_card=agent_card,
    )

    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/.well-known/agent-card.json'
    )
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=handler_extended,
        rpc_url='/',
        enable_v0_3_compat=True,
    )
    rest_routes = create_rest_routes(
        request_handler=handler,
        enable_v0_3_compat=True,
    )

    app = FastAPI()
    app.mount(
        '/jsonrpc',
        FastAPI(routes=jsonrpc_routes + agent_card_routes),
    )
    app.mount('/rest', FastAPI(routes=rest_routes + agent_card_routes))

    server = grpc.aio.server()

    compat_servicer = CompatGrpcHandler(handler)
    a2a_v0_3_pb2_grpc.add_A2AServiceServicer_to_server(compat_servicer, server)
    servicer = GrpcHandler(handler)
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)

    server.add_insecure_port(f'127.0.0.1:{grpc_port}')
    await server.start()

    logger.info(
        'Starting ITK v10 Agent on HTTP port %s and gRPC port %s',
        http_port,
        grpc_port,
    )

    config = uvicorn.Config(
        app, host='127.0.0.1', port=http_port, log_level='info'
    )
    uvicorn_server = uvicorn.Server(config)

    # Signal handling
    loop = asyncio.get_running_loop()

    async def shutdown() -> None:
        logger.info('Shutting down...')
        uvicorn_server.should_exit = True
        await server.stop(5)
        await httpx_client.aclose()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    await uvicorn_server.serve()


def main() -> None:
    """Main entry point for the agent."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--httpPort', type=int, default=10102)
    parser.add_argument('--grpcPort', type=int, default=11002)
    args = parser.parse_args()

    asyncio.run(main_async(args.httpPort, args.grpcPort))


if __name__ == '__main__':
    main()
