import asyncio

from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Request
from pydantic import BaseModel, ValidationError

from a2a.types import Task


class Notification(BaseModel):
    """Encapsulates default push notification data."""

    task: Task
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
            task = Task.model_validate(await request.json())
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))

        async with store_lock:
            if task.id not in store:
                store[task.id] = []
            store[task.id].append(
                Notification(
                    task=task,
                    token=token,
                )
            )
        return {
            'status': 'received',
        }

    @app.get('/tasks/{task_id}/notifications')
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
