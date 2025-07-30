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
            return SendMessageResponse(root=JSONRPCErrorResponse(error=e.error))

    async def send_message_streaming(
        self,
        request: SendStreamingMessageRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[SendStreamingMessageResponse, None]:
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
            return CancelTaskResponse(root=JSONRPCErrorResponse(error=e.error))

    async def set_task_callback(
        self,
        request: SetTaskPushNotificationConfigRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> SetTaskPushNotificationConfigResponse:
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
                root=JSONRPCErrorResponse(error=e.error)
            )

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> GetTaskPushNotificationConfigResponse:
        if not context and http_kwargs:
            context = ClientCallContext(state={'http_kwargs': http_kwargs})
        try:
            result = await self._transport.get_task_callback(
                request.params, context=context
            )
            return GetTaskPushNotificationConfigResponse(
                root=GetTaskPushNotificationConfigSuccessResponse(
                    id=request.id, jsonrpc='2.0', result=result
                )
            )
        except A2AClientJSONRPCError as e:
            return GetTaskPushNotificationConfigResponse(
                root=JSONRPCErrorResponse(error=e.error)
            )

    async def resubscribe(
        self,
        request: TaskResubscriptionRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[SendStreamingMessageResponse, None]:
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
        if not context and http_kwargs:
            context = ClientCallContext(state={'http_kwargs': http_kwargs})
        return await self._transport.get_card(context=context)
