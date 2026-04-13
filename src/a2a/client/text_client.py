import uuid

from a2a.client.client import Client, ClientCallContext
from a2a.types import Message, Part, Role, SendMessageRequest


class TextClient:
    """A facade around Client that simplifies text-based communication.

    Wraps an underlying Client instance and exposes a simplified interface
    for sending plain-text messages and receiving aggregated text responses.
    For full Client API access, use the underlying client directly via
    the `client` property.
    """

    def __init__(self, client: Client):
        self._client = client

    @property
    def client(self) -> Client:
        """Returns the underlying Client instance for full API access."""
        return self._client

    async def send_text_message(
        self,
        text: str,
        *,
        context: ClientCallContext | None = None,
    ) -> str:
        """Sends a text message and returns the aggregated text response."""
        request = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                message_id=str(uuid.uuid4()),
                parts=[Part(text=text)],
            )
        )

        response_parts: list[str] = []

        async for event in self._client.send_message(request, context=context):
            if event.HasField('message'):
                response_parts.extend(
                    part.text for part in event.message.parts if part.text
                )
            elif event.HasField('status_update'):
                if event.status_update.status.HasField('message'):
                    response_parts.extend(
                        part.text
                        for part in event.status_update.status.message.parts
                        if part.text
                    )
            elif event.HasField('artifact_update'):
                response_parts.extend(
                    part.text
                    for part in event.artifact_update.artifact.parts
                    if part.text
                )

        return ' '.join(response_parts)

    async def close(self) -> None:
        """Closes the underlying client."""
        await self._client.close()
