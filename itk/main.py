import argparse  # noqa: I001
import asyncio
import base64
import logging
import uuid

import grpc
import httpx
import uvicorn

from fastapi import FastAPI

from pyproto import instruction_pb2

from a2a.client import ClientConfig, ClientFactory
from a2a.compat.v0_3 import a2a_v0_3_pb2_grpc
from a2a.compat.v0_3.grpc_handler import CompatGrpcHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication, A2ARESTFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers import DefaultRequestHandler, GrpcHandler
from a2a.server.tasks import TaskUpdater
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Message,
    Part,
    SendMessageRequest,
    TaskState,
)
from a2a.utils import TransportProtocol


logging.basicConfig(level=logging.INFO)
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


async def handle_call_agent(call: instruction_pb2.CallAgent) -> list[str]:
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
    config.httpx_client = httpx.AsyncClient(timeout=30.0)
    config.grpc_channel_factory = grpc.aio.insecure_channel
    config.supported_protocol_bindings = [selected_transport]
    config.streaming = call.streaming or (
        selected_transport == TransportProtocol.GRPC
    )

    try:
        client = await ClientFactory.connect(
            call.agent_card_uri,
            client_config=config,
        )

        # Wrap nested instruction
        nested_msg = wrap_instruction_to_request(call.instruction)
        request = SendMessageRequest(message=nested_msg)

        results = []
        async for event in client.send_message(request):
            # Event is streaming response and task
            logger.info('Event: %s', event)
            stream_resp, task = event

            message = None
            if stream_resp.HasField('message'):
                message = stream_resp.message
            elif task and task.status.HasField('message'):
                message = task.status.message
            elif stream_resp.HasField(
                'status_update'
            ) and stream_resp.status_update.status.HasField('message'):
                message = stream_resp.status_update.status.message

            if message:
                results.extend(part.text for part in message.parts if part.text)

    except Exception as e:
        logger.exception('Failed to call outbound agent')
        raise RuntimeError(
            f'Outbound call to {call.agent_card_uri} failed: {e!s}'
        ) from e
    else:
        return results


async def handle_instruction(inst: instruction_pb2.Instruction) -> list[str]:
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

        await task_updater.update_status(TaskState.TASK_STATE_SUBMITTED)
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

        try:
            logger.info('Instruction: %s', instruction)
            results = await handle_instruction(instruction)
            response_text = '\n'.join(results)
            logger.info('Response: %s', response_text)
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
                message=task_updater.new_agent_message([Part(text=str(e))]),
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
    handler = DefaultRequestHandler(
        agent_executor=V10AgentExecutor(),
        task_store=task_store,
        queue_manager=InMemoryQueueManager(),
    )

    app = FastAPI()

    json_rpc_app = A2AFastAPIApplication(
        agent_card, handler, enable_v0_3_compat=True
    ).build()
    app.mount('/jsonrpc', json_rpc_app)
    rest_app = A2ARESTFastAPIApplication(
        http_handler=handler, agent_card=agent_card, enable_v0_3_compat=True
    ).build()
    app.mount('/rest', rest_app)

    server = grpc.aio.server()

    compat_servicer = CompatGrpcHandler(agent_card, handler)
    a2a_v0_3_pb2_grpc.add_A2AServiceServicer_to_server(compat_servicer, server)
    servicer = GrpcHandler(agent_card, handler)
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

    await uvicorn_server.serve()


def str2bool(v: str | bool) -> bool:
    """Converts a string to a boolean value."""
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    if v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    raise argparse.ArgumentTypeError('Boolean value expected.')


def main() -> None:
    """Main entry point for the agent."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--httpPort', type=int, default=10102)
    parser.add_argument('--grpcPort', type=int, default=11002)
    args = parser.parse_args()

    asyncio.run(main_async(args.httpPort, args.grpcPort))


if __name__ == '__main__':
    main()
