import asyncio

from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from a2a.types.a2a_pb2 import StreamResponse, Task
from google.protobuf.json_format import ParseDict, MessageToDict


class Notification(BaseModel):
    """Encapsulates default push notification data."""

    task: dict[str, Any]
    token: str


def create_notifications_app() -> FastAPI:
    """Creates a simple push notification ingesting HTTP+REST application."""
    app = FastAPI()
    store_lock = asyncio.Lock()
    store: dict[str, list[Notification]] = {}

    @app.post('/notifications')
    async def add_notification(request: Request):
        """Endpoint for ingesting notifications from agents. It receives a JSON
        payload and stores it in-memory.
        """
        token = request.headers.get('x-a2a-notification-token')
        if not token:
            raise HTTPException(
                status_code=400,
                detail='Missing "x-a2a-notification-token" header.',
            )
        try:
            json_data = await request.json()
            stream_response = ParseDict(json_data, StreamResponse())
            if not stream_response.HasField('task'):
                raise HTTPException(
                    status_code=400, detail='Missing task in StreamResponse'
                )
            task = stream_response.task
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        async with store_lock:
            if task.id not in store:
                store[task.id] = []
            store[task.id].append(
                Notification(
                    task=MessageToDict(task, preserving_proto_field_name=True),
                    token=token,
                )
            )
        return {
            'status': 'received',
        }

    @app.get('/{task_id}/notifications')
    async def list_notifications_by_task(
        task_id: Annotated[
            str, Path(title='The ID of the task to list the notifications for.')
        ],
    ):
        """Helper endpoint for retrieving ingested notifications for a given task."""
        async with store_lock:
            notifications = store.get(task_id, [])
        return {'notifications': notifications}

    @app.get('/health')
    def health_check():
        """Helper endpoint for checking if the server is up."""
        return {'status': 'ok'}

    return app
