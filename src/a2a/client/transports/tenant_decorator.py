from collections.abc import AsyncGenerator

from a2a.client.client import ClientCallContext
from a2a.client.transports.base import ClientTransport
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)


class TenantTransportDecorator(ClientTransport):
    """A transport decorator that attaches a tenant to all requests."""

    def __init__(self, base: ClientTransport, tenant: str):
        self._base = base
        self._tenant = tenant

    def _resolve_tenant(self, tenant: str) -> str:
        """If tenant is not provided, use the default tenant.

        Returns:
            The tenant used for the request.
        """
        return tenant or self._tenant

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> SendMessageResponse:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        request.tenant = self._resolve_tenant(request.tenant)
        return await self._base.send_message(request, context=context)

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Sends a streaming message request to the agent and yields responses."""
        request.tenant = self._resolve_tenant(request.tenant)
        async for event in self._base.send_message_streaming(
            request, context=context
        ):
            yield event

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        request.tenant = self._resolve_tenant(request.tenant)
        return await self._base.get_task(request, context=context)

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        request.tenant = self._resolve_tenant(request.tenant)
        return await self._base.list_tasks(request, context=context)

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        request.tenant = self._resolve_tenant(request.tenant)
        return await self._base.cancel_task(request, context=context)

    async def create_task_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        request.tenant = self._resolve_tenant(request.tenant)
        return await self._base.create_task_push_notification_config(
            request, context=context
        )

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        request.tenant = self._resolve_tenant(request.tenant)
        return await self._base.get_task_push_notification_config(
            request, context=context
        )

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task."""
        request.tenant = self._resolve_tenant(request.tenant)
        return await self._base.list_task_push_notification_configs(
            request, context=context
        )

    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Deletes the push notification configuration for a specific task."""
        request.tenant = self._resolve_tenant(request.tenant)
        await self._base.delete_task_push_notification_config(
            request, context=context
        )

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Reconnects to get task updates."""
        request.tenant = self._resolve_tenant(request.tenant)
        async for event in self._base.subscribe(request, context=context):
            yield event

    async def get_extended_agent_card(
        self,
        request: GetExtendedAgentCardRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieves the Extended AgentCard."""
        request.tenant = self._resolve_tenant(request.tenant)
        return await self._base.get_extended_agent_card(
            request,
            context=context,
        )

    async def close(self) -> None:
        """Closes the transport."""
        await self._base.close()
