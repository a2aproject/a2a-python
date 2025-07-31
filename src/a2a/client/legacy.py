"""Backwards compatibility layer for legacy A2A clients."""

import warnings

from collections.abc import AsyncGenerator
from typing import Any

import httpx

from a2a.client.errors import A2AClientJSONRPCError
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    CancelTaskResponse,
    CancelTaskSuccessResponse,
    GetTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigResponse,
    GetTaskPushNotificationConfigSuccessResponse,
    GetTaskRequest,
    GetTaskResponse,
    GetTaskSuccessResponse,
    JSONRPCErrorResponse,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageSuccessResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    SendStreamingMessageSuccessResponse,
    SetTaskPushNotificationConfigRequest,
    SetTaskPushNotificationConfigResponse,
    SetTaskPushNotificationConfigSuccessResponse,
    TaskIdParams,
    TaskResubscriptionRequest,
)


class A2AClient:
    """[DEPRECATED] Backwards compatibility wrapper for the JSON-RPC client."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard | None = None,
        url: str | None = None,
        interceptors: list[ClientCallInterceptor] | None = None,
    ):
        warnings.warn(
            'A2AClient is deprecated and will be removed in a future version. '
            'Use ClientFactory to create a client with a JSON-RPC transport.',
            DeprecationWarning,
            stacklevel=2,
        )
        self._transport = JsonRpcTransport(
            httpx_client, agent_card, url, interceptors
        )

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> SendMessageResponse:
        """Sends a non-streaming message request to the agent.

        Args:
            request: The `SendMessageRequest` object containing the message and configuration.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `SendMessageResponse` object containing the agent's response (Task or Message) or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not context and http_kwargs:
            context = ClientCallContext(state={'http_kwargs': http_kwargs})

        try:
            result = await self._transport.send_message(
                request.params, context=context
            )
            return SendMessageResponse(
                root=SendMessageSuccessResponse(
                    id=request.id, jsonrpc='2.0', result=result
                )
            )
        except A2AClientJSONRPCError as e:
            return SendMessageResponse(JSONRPCErrorResponse(error=e.error))

    async def send_message_streaming(
        self,
        request: SendStreamingMessageRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[SendStreamingMessageResponse, None]:
        """Sends a streaming message request to the agent and yields responses as they arrive.

        This method uses Server-Sent Events (SSE) to receive a stream of updates from the agent.

        Args:
            request: The `SendStreamingMessageRequest` object containing the message and configuration.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request. A default `timeout=None` is set but can be overridden.
            context: The client call context.

        Yields:
            `SendStreamingMessageResponse` objects as they are received in the SSE stream.
            These can be Task, Message, TaskStatusUpdateEvent, or TaskArtifactUpdateEvent.

        Raises:
            A2AClientHTTPError: If an HTTP or SSE protocol error occurs during the request.
            A2AClientJSONError: If an SSE event data cannot be decoded as JSON or validated.
        """
        if not context and http_kwargs:
            context = ClientCallContext(state={'http_kwargs': http_kwargs})

        async for result in self._transport.send_message_streaming(
            request.params, context=context
        ):
            yield SendStreamingMessageResponse(
                root=SendStreamingMessageSuccessResponse(
                    id=request.id, jsonrpc='2.0', result=result
                )
            )

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> GetTaskResponse:
        """Retrieves the current state and history of a specific task.

        Args:
            request: The `GetTaskRequest` object specifying the task ID and history length.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `GetTaskResponse` object containing the Task or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not context and http_kwargs:
            context = ClientCallContext(state={'http_kwargs': http_kwargs})
        try:
            result = await self._transport.get_task(
                request.params, context=context
            )
            return GetTaskResponse(
                root=GetTaskSuccessResponse(
                    id=request.id, jsonrpc='2.0', result=result
                )
            )
        except A2AClientJSONRPCError as e:
            return GetTaskResponse(root=JSONRPCErrorResponse(error=e.error))

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> CancelTaskResponse:
        """Requests the agent to cancel a specific task.

        Args:
            request: The `CancelTaskRequest` object specifying the task ID.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `CancelTaskResponse` object containing the updated Task with canceled status or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not context and http_kwargs:
            context = ClientCallContext(state={'http_kwargs': http_kwargs})
        try:
            result = await self._transport.cancel_task(
                request.params, context=context
            )
            return CancelTaskResponse(
                root=CancelTaskSuccessResponse(
                    id=request.id, jsonrpc='2.0', result=result
                )
            )
        except A2AClientJSONRPCError as e:
            return CancelTaskResponse(JSONRPCErrorResponse(error=e.error))

    async def set_task_callback(
        self,
        request: SetTaskPushNotificationConfigRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> SetTaskPushNotificationConfigResponse:
        """Sets or updates the push notification configuration for a specific task.

        Args:
            request: The `SetTaskPushNotificationConfigRequest` object specifying the task ID and configuration.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `SetTaskPushNotificationConfigResponse` object containing the confirmation or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not context and http_kwargs:
            context = ClientCallContext(state={'http_kwargs': http_kwargs})
        try:
            result = await self._transport.set_task_callback(
                request.params, context=context
            )
            return SetTaskPushNotificationConfigResponse(
                root=SetTaskPushNotificationConfigSuccessResponse(
                    id=request.id, jsonrpc='2.0', result=result
                )
            )
        except A2AClientJSONRPCError as e:
            return SetTaskPushNotificationConfigResponse(
                JSONRPCErrorResponse(error=e.error)
            )

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> GetTaskPushNotificationConfigResponse:
        """Retrieves the push notification configuration for a specific task.

        Args:
            request: The `GetTaskPushNotificationConfigRequest` object specifying the task ID.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `GetTaskPushNotificationConfigResponse` object containing the configuration or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not context and http_kwargs:
            context = ClientCallContext(state={'http_kwargs': http_kwargs})
        params = request.params
        if isinstance(params, TaskIdParams):
            params = GetTaskPushNotificationConfigParams(id=request.params.id)
        try:
            result = await self._transport.get_task_callback(
                params, context=context
            )
            return GetTaskPushNotificationConfigResponse(
                root=GetTaskPushNotificationConfigSuccessResponse(
                    id=request.id, jsonrpc='2.0', result=result
                )
            )
        except A2AClientJSONRPCError as e:
            return GetTaskPushNotificationConfigResponse(
                JSONRPCErrorResponse(error=e.error)
            )

    async def resubscribe(
        self,
        request: TaskResubscriptionRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[SendStreamingMessageResponse, None]:
        """Reconnects to get task updates.

        This method uses Server-Sent Events (SSE) to receive a stream of updates from the agent.

        Args:
            request: The `TaskResubscriptionRequest` object containing the task information to reconnect to.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request. A default `timeout=None` is set but can be overridden.
            context: The client call context.

        Yields:
            `SendStreamingMessageResponse` objects as they are received in the SSE stream.
            These can be Task, Message, TaskStatusUpdateEvent, or TaskArtifactUpdateEvent.

        Raises:
            A2AClientHTTPError: If an HTTP or SSE protocol error occurs during the request.
            A2AClientJSONError: If an SSE event data cannot be decoded as JSON or validated.
        """
        if not context and http_kwargs:
            context = ClientCallContext(state={'http_kwargs': http_kwargs})

        async for result in self._transport.resubscribe(
            request.params, context=context
        ):
            yield SendStreamingMessageResponse(
                root=SendStreamingMessageSuccessResponse(
                    id=request.id, jsonrpc='2.0', result=result
                )
            )

    async def get_card(
        self,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieves the authenticated card (if necessary) or the public one.

        Args:
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `AgentCard` object containing the card or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not context and http_kwargs:
            context = ClientCallContext(state={'http_kwargs': http_kwargs})
        return await self._transport.get_card(context=context)
