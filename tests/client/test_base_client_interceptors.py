# ruff: noqa: INP001
from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.client.base_client import BaseClient
from a2a.client.client import ClientConfig
from a2a.client.interceptors import (
    AfterArgs,
    BeforeArgs,
    ClientCallInput,
    ClientCallInterceptor,
    ClientCallResult,
)
from a2a.client.transports.base import ClientTransport
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
)


@pytest.fixture
def mock_transport() -> AsyncMock:
    return AsyncMock(spec=ClientTransport)


@pytest.fixture
def sample_agent_card() -> AgentCard:
    return AgentCard(
        name='Test Agent',
        description='An agent for testing',
        supported_interfaces=[
            AgentInterface(url='http://test.com', protocol_binding='HTTP+JSON')
        ],
        version='1.0',
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[],
    )


@pytest.fixture
def mock_interceptor() -> AsyncMock:
    return AsyncMock(spec=ClientCallInterceptor)


@pytest.fixture
def base_client(
    sample_agent_card: AgentCard,
    mock_transport: AsyncMock,
    mock_interceptor: AsyncMock,
) -> BaseClient:
    config = ClientConfig(streaming=True)
    return BaseClient(
        card=sample_agent_card,
        config=config,
        transport=mock_transport,
        consumers=[],
        interceptors=[mock_interceptor],
    )


class TestBaseClientInterceptors:
    @pytest.mark.asyncio
    async def test_execute_with_interceptors_normal_flow(
        self,
        base_client: BaseClient,
        mock_interceptor: AsyncMock,
    ):
        input_data = ClientCallInput(method='get_task', value=MagicMock())
        context = MagicMock()
        mock_transport_call = AsyncMock(return_value='transport_result')

        # Set up mock interceptor to just pass through
        mock_interceptor.before.return_value = None

        result = await base_client._execute_with_interceptors(
            input_data=input_data,
            context=context,
            transport_call=mock_transport_call,
        )

        assert result == 'transport_result'

        # Verify before was called
        mock_interceptor.before.assert_called_once()
        before_args = mock_interceptor.before.call_args[0][0]
        assert isinstance(before_args, BeforeArgs)
        assert before_args.input == input_data
        assert before_args.context == context

        # Verify transport call was made
        mock_transport_call.assert_called_once_with(input_data.value, context)

        # Verify after was called
        mock_interceptor.after.assert_called_once()
        after_args = mock_interceptor.after.call_args[0][0]
        assert isinstance(after_args, AfterArgs)
        assert after_args.result.method == input_data.method
        assert after_args.result.value == 'transport_result'
        assert after_args.context == context

    @pytest.mark.asyncio
    async def test_execute_with_interceptors_early_return(
        self,
        base_client: BaseClient,
        mock_interceptor: AsyncMock,
    ):
        input_data = ClientCallInput(method='get_task', value=MagicMock())
        context = MagicMock()
        mock_transport_call = AsyncMock()

        # Set up early return in before
        early_return_result = ClientCallResult(
            method='get_task', value='early_result'
        )

        async def mock_before_with_early_return(args: BeforeArgs):
            args.early_return = early_return_result

        mock_interceptor.before.side_effect = mock_before_with_early_return

        result = await base_client._execute_with_interceptors(
            input_data=input_data,
            context=context,
            transport_call=mock_transport_call,
        )

        assert result == 'early_result'

        # Verify before was called
        mock_interceptor.before.assert_called_once()

        # Verify transport call was NOT made
        mock_transport_call.assert_not_called()

        # Verify after was called with early return value
        mock_interceptor.after.assert_called_once()
        after_args = mock_interceptor.after.call_args[0][0]
        assert isinstance(after_args, AfterArgs)
        assert after_args.result.value == 'early_result'
        assert after_args.context == context
