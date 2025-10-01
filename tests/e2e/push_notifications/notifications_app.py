import asyncio

from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Request


def create_notifications_app() -> FastAPI:
    """Creates a simple push notification injesting HTTP+REST application."""
    app = FastAPI()
    store_lock = asyncio.Lock()
    store: dict[str, list] = {}

    @app.post('/notifications')
    async def add_notification(request: Request):
        """Endpoint for injesting notifications from agents. It receives a JSON
        payload and stores it in-memory.
        """
        if not request.headers.get('x-a2a-notification-token'):
            raise HTTPException(
                status_code=400,
                detail='Missing "x-a2a-notification-token" header.',
            )
        payload = await request.json()
        task_id = payload.get('id')
        if not task_id:
            raise HTTPException(
                status_code=400, detail='Missing "id" in notification payload.'
            )
        async with store_lock:
            if task_id not in store:
                store[task_id] = []
            store[task_id].append(payload)
        return {
            'status': 'received',
        }

    @app.get('/tasks/{task_id}/notifications')
    async def list_notifications_by_task(
        task_id: Annotated[
            str, Path(title='The ID of the task to list the notifications for.')
        ],
    ):
        """Helper endpoint for retrieving injested notifications for a given task."""
        async with store_lock:
            notifications = store.get(task_id, [])
        return {'notifications': notifications}

    @app.get('/health')
    def health_check():
        """Helper endpoint for checking if the server is up."""
        return {'status': 'ok'}

    return app
