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
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp
from typing import Any

from pyproto import instruction_pb2

import acts_behaviors

from a2a.client import Client, ClientConfig, create_client
from a2a.client.errors import A2AClientError
from a2a.compat.v0_3 import a2a_v0_3_pb2_grpc
from a2a.compat.v0_3.grpc_handler import CompatGrpcHandler
from a2a.compat.v0_3.types import Role as LegacyRole
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
from a2a.types import Role, a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    CancelTaskRequest,
    HTTPAuthSecurityScheme,
    Message,
    Part,
    SecurityRequirement,
    SecurityScheme,
    SendMessageRequest,
    StringList,
    SubscribeToTaskRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskPushNotificationConfig,
)
from a2a.utils import TransportProtocol

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
            except Exception:
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
            except Exception:
                logger.debug(
                    'Failed to parse instruction from text part', exc_info=True
                )
                continue
            else:
                return inst

    return None


def _get_text_from_part(part: Any) -> str | None:
    """Safely extracts text string from a Part object supporting protobuf, pydantic, and raw dict."""
    if not part:
        return None
    if hasattr(part, 'HasField') and part.HasField('text'):
        return part.text
    root = getattr(part, 'root', part)
    if isinstance(root, dict):
        return root.get('text')
    return getattr(root, 'text', None)


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


async def _handle_call_agent_with_resubscribe(  # noqa: PLR0912, PLR0915
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
        if isinstance(event, Task):
            task_obj = event
        elif hasattr(event, 'HasField') and event.HasField('task'):
            task_obj = event.task

        if task_obj and hasattr(task_obj, 'history'):
            for msg in task_obj.history:
                if msg.role in (
                    Role.ROLE_AGENT,
                    LegacyRole.agent,
                    'ROLE_AGENT',
                ):
                    for part in msg.parts:
                        text = _get_text_from_part(part)
                        if text and 'task-finished' in text:
                            logger.info(
                                'Found task-finished in history, breaking loop!'
                            )
                            results.append(text.replace('task-finished', ''))
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
        if any('task-finished' in text for text in extracted_text):
            logger.info(
                'Received task-finished after re-subscribe, breaking loop.'
            )
            finished = True
            break

    if not results and task_obj and hasattr(task_obj, 'history'):
        logger.info('Results empty after loop, reading from history.')
        for msg in task_obj.history:
            # Check role using SDK schemas for v1.0 (protobuf enum Role.ROLE_AGENT)
            # and v0.3 (pydantic enum LegacyRole.agent), as well as string forms.
            if msg.role in (Role.ROLE_AGENT, LegacyRole.agent, 'ROLE_AGENT'):
                for part in msg.parts:
                    text = _get_text_from_part(part)
                    if text:
                        results.append(text.replace('task-finished', ''))

    if not finished:
        logger.info('Canceling task %s after retrieval.', task_id)
        try:
            await client.cancel_task(CancelTaskRequest(id=task_id))
            logger.info('Task cancelled successfully: %s', task_id)
        except A2AClientError:
            logger.exception('Failed to cancel task %s', task_id)
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
            token='itk-token',  # noqa: S106
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

        # Dual mode. An ACTS conformance test names a `tck-*` behaviour in its
        # first user message (ACTS §11); anything else is an ITK traversal
        # carrying a protobuf Instruction. The branch is taken before any task
        # is created, because one ACTS behaviour must answer with a bare
        # Message and so must not open a task at all.
        behavior = acts_behaviors.behavior_for(context)
        if behavior is not None:
            await acts_behaviors.run(behavior, context, event_queue)
            return

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
                    'Emitting response and task-finished for held task %s',
                    context.task_id,
                )
                await task_updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=task_updater.new_agent_message(
                        [Part(text=response_text + '\n' + 'task-finished')]
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
        except Exception:
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


#: Credentials the ACTS runner presents. Not secrets: the runner attaches the
#: valid one to every abstract operation and offers the insufficient one from
#: `SEC-AUTH-002` and `SEC-EXTCARD-002`, so a fixture has to recognise both to
#: answer 200 / 403 / 401 as those tests require.
ACTS_VALID_TOKEN = 'itk-valid-token'  # noqa: S105
ACTS_INSUFFICIENT_TOKEN = 'itk-insufficient-token'  # noqa: S105
ACTS_SECURITY_SCHEME = 'bearerAuth'

#: Where the REST binding serves the extended card, relative to its mount.
#: Matched as a suffix because `create_rest_routes` also mounts every route
#: under `/{tenant}`, so the same operation answers on two paths.
EXTENDED_CARD_PATH = '/extendedAgentCard'


def _auth_enforced() -> bool:
    """Whether to require a credential on the ordinary operation endpoints.

    Off unless `ITK_ACTS_AUTH` is set. Two reasons, and the second decides it:

    - ITK traversal peers dial this agent with no credential at all, so
      enforcing during a traversal run would fail every scenario that calls
      us. An environment switch alone would handle that, since the two suites
      run as separate processes.
    - The ACTS runner attaches its credential to *abstract operations only*;
      raw steps are sent exactly as written (ACTS §4.4), which is what keeps
      the unauthenticated `SEC-AUTH-001` probe meaningful. But then every
      other raw step is unauthenticated too, and fifteen of them expect to
      succeed. An absent `Authorization` header means "reject me" in
      `SEC-AUTH-001` and "serve me" in `JSONRPC-ENV-001`, and no server can
      tell those two requests apart.

    So the ACTS runner sets this for a *separate* pass over just the
    `SEC-AUTH-*` tests, the same way `ITK_ACTS_REDUCED_CAPABILITIES` gets its
    own pass, and leaves the main pass unauthenticated. With the switch off
    the card declares no schemes — which is honest, and makes those tests skip
    on their `authentication` precondition rather than fail.

    The extended-card endpoint is *not* covered by this switch; see
    `_ActsCredentialMiddleware`.
    """
    return bool(os.environ.get('ITK_ACTS_AUTH'))


def _security_schemes() -> dict[str, SecurityScheme]:
    """The schemes the card advertises.

    Declared only when the agent actually enforces them: a card claiming a
    scheme it does not check would be a lie, and this is what the ACTS
    `authentication` precondition reads to decide whether the `SEC-AUTH-*`
    tests are applicable at all.
    """
    if not _auth_enforced():
        return {}
    return {
        ACTS_SECURITY_SCHEME: SecurityScheme(
            http_auth_security_scheme=HTTPAuthSecurityScheme(
                description='Bearer token presented by the ACTS runner.',
                scheme='Bearer',
                bearer_format='opaque',
            )
        )
    }


def _security_requirements() -> list[SecurityRequirement]:
    """What the card says a client must satisfy.

    Separate from the schemes because they mean different things: schemes are
    what a client *may* use, requirements are what it *must*. An agent
    publishing the first and not the second requires nothing.
    """
    if not _auth_enforced():
        return []
    return [SecurityRequirement(schemes={ACTS_SECURITY_SCHEME: StringList()})]


def _status_body(code: int, status: str, message: str) -> dict[str, Any]:
    """A `google.rpc.Status` body, the shape A2A §11.6 requires of an error."""
    return {
        'error': {
            'code': code,
            'status': status,
            'message': message,
            'details': [
                {
                    '@type': 'type.googleapis.com/google.rpc.ErrorInfo',
                    'reason': status,
                    'domain': 'a2a-protocol.org',
                }
            ],
        }
    }


def _credential_rejection(request: Request) -> JSONResponse | None:
    """The response refusing this request, or None to let it through.

    Three outcomes, because the tests distinguish them: the valid token
    passes, the insufficient one authenticates but does not authorize (403),
    and anything else — including nothing at all — fails authentication (401).
    The `WWW-Authenticate` challenge is what A2A §3.3.2 asks for on the 401.
    """
    header = request.headers.get('authorization', '')
    presented = ''
    scheme, _, value = header.partition(' ')
    if scheme.lower() == 'bearer':
        presented = value.strip()

    if presented == ACTS_VALID_TOKEN:
        return None
    if presented == ACTS_INSUFFICIENT_TOKEN:
        return JSONResponse(
            _status_body(
                403, 'PERMISSION_DENIED', 'Token lacks the required scope.'
            ),
            status_code=403,
        )
    return JSONResponse(
        _status_body(401, 'UNAUTHENTICATED', 'A bearer token is required.'),
        status_code=401,
        headers={
            'WWW-Authenticate': (
                f'Bearer realm="a2a", scheme="{ACTS_SECURITY_SCHEME}"'
            )
        },
    )


class _ActsCredentialMiddleware(BaseHTTPMiddleware):
    """Guards a binding's operations, and always guards the extended card.

    The extended card is guarded whatever `ITK_ACTS_AUTH` says, for two
    reasons. It costs traversal nothing — no traversal scenario fetches one —
    and A2A §13.3 makes it unconditional: the operation MUST require
    authentication, whether or not the agent requires it anywhere else. That
    is why `SEC-EXTCARD-001/002/004` are the only auth tests that can produce
    a verdict against a default SUT.

    The public agent card is not reachable through this middleware: it is
    served from the parent app, not from either binding's mount. It has to
    stay open — A2A §8.2 makes the well-known URL the discovery mechanism and
    §7.3 has the client learn which schemes it needs *from that card*, so
    requiring one to read it would be circular, and the ITK readiness probe
    fetches it unauthenticated.
    """

    def __init__(self, app: ASGIApp, *, guard_all: bool) -> None:
        super().__init__(app)
        self._guard_all = guard_all

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        guarded = self._guard_all or request.url.path.endswith(
            EXTENDED_CARD_PATH
        )
        if not guarded:
            return await call_next(request)
        rejection = _credential_rejection(request)
        if rejection is not None:
            return rejection
        return await call_next(request)


def _capabilities() -> AgentCapabilities:
    """What this agent advertises — everything, unless asked for less.

    Four ACTS tests assert that an agent *without* a capability answers
    `UnsupportedOperationError`, so their preconditions require the card not
    to advertise it and they can never run against a fully capable agent. The
    ACTS runner starts a second SUT with `ITK_ACTS_REDUCED_CAPABILITIES` set
    to reach them, and the SDK already gates those operations on this card, so
    publishing less is all it takes to refuse them.
    """
    if os.environ.get('ITK_ACTS_REDUCED_CAPABILITIES'):
        logger.info('Advertising no optional capabilities (ACTS reduced pass)')
        return AgentCapabilities(
            streaming=False,
            push_notifications=False,
            extended_agent_card=False,
        )
    return AgentCapabilities(
        streaming=True,
        push_notifications=True,
        extended_agent_card=True,
    )


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
        # ACTS evaluates a test's `preconditions` against this card and skips
        # when they are unmet (ACTS §12.5), so anything the agent really does
        # has to be advertised or the matching tests silently never run.
        capabilities=_capabilities(),
        # Authentication is *not* a capability — `AgentCapabilities` has no
        # member for it — so it is declared here, at the top level, and the
        # ACTS `authentication` precondition reads both of these.
        security_schemes=_security_schemes(),
        security_requirements=_security_requirements(),
        # application/x-protobuf because the ITK instruction envelope is a
        # binary Part: wrap_instruction_to_request() builds one and
        # extract_instruction() reads it. The agent has always accepted
        # those; declaring text/plain alone was a card that understated
        # what it takes, which went unnoticed until validate_input_modes
        # below started holding the agent to it.
        default_input_modes=['text/plain', 'application/x-protobuf'],
        default_output_modes=['text/plain'],
        supported_interfaces=interfaces,
        skills=[
            AgentSkill(
                id='acts-behaviors',
                name='ACTS behaviours',
                description='Implements the ACTS §11 tck-* behaviour contract.',
                tags=['acts', 'conformance'],
            )
        ],
    )

    task_store = InMemoryTaskStore()
    push_config_store = InMemoryPushNotificationConfigStore()
    httpx_client = httpx.AsyncClient()
    push_sender = BasePushNotificationSender(
        httpx_client=httpx_client,
        config_store=push_config_store,
    )

    # One handler for every binding. It carries `extended_agent_card` because
    # the card advertises `extendedAgentCard: true`, and a capability is
    # advertised per agent, not per binding — configuring it on JSON-RPC alone
    # made `Get Extended Agent Card` answer with the card over JSON-RPC and
    # `ExtendedAgentCardNotConfiguredError` over gRPC and REST, from an agent
    # claiming the capability once for all three.
    handler = DefaultRequestHandler(
        agent_executor=V10AgentExecutor(),
        agent_card=agent_card,
        task_store=task_store,
        queue_manager=InMemoryQueueManager(),
        push_config_store=push_config_store,
        push_sender=push_sender,
        extended_agent_card=agent_card,
        # CORE-SEND-004 sends a part whose mediaType the card does not
        # declare and expects ContentTypeNotSupportedError. Off by default in
        # the SDK; this card states its input modes accurately, so the
        # fixture can hold the SDK to them.
        validate_input_modes=True,
    )

    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/.well-known/agent-card.json'
    )
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=handler,
        rpc_url='/',
        enable_v0_3_compat=True,
    )
    rest_routes = create_rest_routes(
        request_handler=handler,
        enable_v0_3_compat=True,
    )

    # Each binding is its own sub-app, so the credential guard goes on the
    # sub-app rather than on a path prefix of the parent: the public card is
    # served from the parent alone and must stay reachable without one.
    jsonrpc_app = FastAPI(routes=jsonrpc_routes)
    rest_app = FastAPI(routes=rest_routes)
    if _auth_enforced():
        logger.info('Requiring a bearer credential on /jsonrpc and /rest')
        jsonrpc_app.add_middleware(_ActsCredentialMiddleware, guard_all=True)
    # Always on for REST, whatever the mode: `/extendedAgentCard` lives here
    # and A2A §13.3 makes its authentication unconditional.
    rest_app.add_middleware(
        _ActsCredentialMiddleware, guard_all=_auth_enforced()
    )

    app = FastAPI()
    app.mount('/jsonrpc', jsonrpc_app)
    app.mount('/rest', rest_app)
    app.routes.extend(agent_card_routes)

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
        app, host='127.0.0.1', port=http_port, log_level=log_level_str.lower()
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
