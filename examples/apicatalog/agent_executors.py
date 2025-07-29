from typing_extensions import override

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message


class HelloWorldAgent:
    """A simple agent that returns a static 'Hello, world!' message."""

    async def invoke(self) -> str:
        """Invokes the agent's main logic and returns a response message.

        Returns:
            str: The fixed message 'Hello, world!'.
        """
        return 'Hello, world!'


class HelloWorldAgentExecutor(AgentExecutor):
    """AgentExecutor implementation for the HelloWorldAgent.

    This executor wraps a HelloWorldAgent and, when executed, sends
    a single text message event with the message "Hello World".

    Intended for demonstration, testing, or HelloWorld scaffolding purposes.
    """

    def __init__(self) -> None:
        """Initializes the executor with a HelloWorldAgent instance."""
        self.agent = HelloWorldAgent()

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Executes the agent by invoking it and emitting the result as a text message event.

        Args:
            context: The request context provided by the framework.
            event_queue: The event queue to which agent messages should be enqueued.
        """
        result = await self.agent.invoke()
        event_queue.enqueue_event(new_agent_text_message(result))

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Raises an exception because cancelation is not supported for this example agent.

        Args:
            context: The request context (not used in this method).
            event_queue: The event queue (not used in this method).

        Raises:
            Exception: Always raised, indicating cancel is not supported.
        """
        raise Exception('cancel not supported')


class EchoAgent:
    """An agent that returns the input message as-is."""

    async def invoke(self, message: str) -> str:
        """Invokes the agent's main logic and returns the input message unchanged.

        This method simulates an echo behavior by returning
        the same message that was passed as input.

        Args:
            message: The input string to echo.

        Returns:
            The same string that was provided as input.
        """
        return message


class EchoAgentExecutor(AgentExecutor):
    """AgentExecutor implementation for the EchoAgent.

    This executor wraps an EchoAgent and, when executed, it sends back
    the same message it receives, simulating a basic echo response.

    Intended for demonstration, testing, or HelloWorld scaffolding purposes.
    """

    def __init__(self) -> None:
        """Initializes the executor with a EchoAgent instance."""
        self.agent = EchoAgent()

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Executes the agent by invoking it and emitting the result as a text message event.

        Args:
            context: The request context provided by the framework.
            event_queue: The event queue to which agent messages should be enqueued.
        """
        message = context.get_user_input()
        result = await self.agent.invoke(message)
        event_queue.enqueue_event(new_agent_text_message(result))

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Raises an exception because cancelation is not supported for this example agent.

        Args:
            context: The request context (not used in this method).
            event_queue: The event queue (not used in this method).

        Raises:
            Exception: Always raised, indicating cancel is not supported.
        """
        raise Exception('cancel not supported')
