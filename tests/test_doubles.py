from collections import defaultdict
from typing import Any

from a2a.server.events.event_queue import Event, EventQueue
from a2a.server.tasks import TaskStore
from a2a.server.tasks.push_notification_config_store import (
    PushNotificationConfigStore,
)
from a2a.types import PushNotificationConfig, Task


class InMemoryTaskStore(TaskStore):
    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._save_count = 0
        self._get_count = 0
        self._delete_count = 0

    async def save(self, task: Task) -> None:
        self._save_count += 1
        self._tasks[task.id] = task

    async def get(self, task_id: str) -> Task | None:
        self._get_count += 1
        return self._tasks.get(task_id)

    async def delete(self, task_id: str) -> None:
        self._delete_count += 1
        self._tasks.pop(task_id, None)

    def assert_saved(self, task_id: str) -> None:
        assert task_id in self._tasks, f'Task {task_id} was not saved'

    def assert_not_saved(self, task_id: str) -> None:
        assert task_id not in self._tasks, f'Task {task_id} should not be saved'

    def assert_save_called(self, times: int = 1) -> None:
        assert self._save_count == times, (
            f'Expected save to be called {times} times, but was called {self._save_count} times'
        )

    def assert_get_called(self, times: int = 1) -> None:
        assert self._get_count == times, (
            f'Expected get to be called {times} times, but was called {self._get_count} times'
        )

    def assert_delete_called(self, times: int = 1) -> None:
        assert self._delete_count == times, (
            f'Expected delete to be called {times} times, but was called {self._delete_count} times'
        )

    def get_saved_task(self, task_id: str) -> Task:
        assert task_id in self._tasks, f'Task {task_id} not found'
        return self._tasks[task_id]

    def set_task(self, task: Task) -> None:
        self._tasks[task.id] = task

    def clear(self) -> None:
        self._tasks.clear()
        self._save_count = 0
        self._get_count = 0
        self._delete_count = 0


class SpyEventQueue(EventQueue):
    def __init__(self):
        self.events: list[Event] = []
        self._closed = False

    async def publish(self, event: Event) -> None:
        if self._closed:
            raise RuntimeError('Cannot publish to closed queue')
        self.events.append(event)

    async def close(self) -> None:
        self._closed = True

    def is_closed(self) -> bool:
        return self._closed

    def assert_event_published(self, event_type: type) -> None:
        assert any(isinstance(e, event_type) for e in self.events), (
            f'No event of type {event_type.__name__} was published'
        )

    def assert_no_event_published(self, event_type: type) -> None:
        assert not any(isinstance(e, event_type) for e in self.events), (
            f'Event of type {event_type.__name__} should not have been published'
        )

    def assert_event_count(self, count: int) -> None:
        assert len(self.events) == count, (
            f'Expected {count} events, but got {len(self.events)}'
        )

    def get_events_of_type(self, event_type: type) -> list[Event]:
        return [e for e in self.events if isinstance(e, event_type)]

    def get_last_event(self) -> Event | None:
        return self.events[-1] if self.events else None

    def clear(self) -> None:
        self.events.clear()
        self._closed = False


class StubPushNotificationConfigStore(PushNotificationConfigStore):
    def __init__(self):
        self._configs: dict[str, list[PushNotificationConfig]] = defaultdict(
            list
        )
        self._set_count = 0
        self._get_count = 0
        self._delete_count = 0

    async def set_info(
        self, task_id: str, config: PushNotificationConfig
    ) -> None:
        self._set_count += 1
        configs = self._configs[task_id]
        if config.id:
            configs = [c for c in configs if c.id != config.id]
        configs.append(config)
        self._configs[task_id] = configs

    async def get_info(self, task_id: str) -> list[PushNotificationConfig]:
        self._get_count += 1
        return self._configs.get(task_id, [])

    async def delete_info(
        self, task_id: str, config_id: str | None = None
    ) -> None:
        self._delete_count += 1
        if config_id:
            self._configs[task_id] = [
                c for c in self._configs.get(task_id, []) if c.id != config_id
            ]
        else:
            self._configs.pop(task_id, None)

    def assert_config_set(self, task_id: str) -> None:
        assert task_id in self._configs, f'No config set for task {task_id}'

    def assert_set_called(self, times: int = 1) -> None:
        assert self._set_count == times, (
            f'Expected set_info to be called {times} times, but was called {self._set_count} times'
        )

    def get_config(self, task_id: str) -> PushNotificationConfig | None:
        configs = self._configs.get(task_id, [])
        return configs[0] if configs else None

    def clear(self) -> None:
        self._configs.clear()
        self._set_count = 0
        self._get_count = 0
        self._delete_count = 0


class FakeHttpClient:
    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []
        self._response_index = 0

    def add_response(
        self,
        status: int,
        json: dict | None = None,
        text: str | None = None,
    ):
        self.responses.append({'status': status, 'json': json, 'text': text})

    async def post(self, url: str, **kwargs):
        self.requests.append({'method': 'POST', 'url': url, **kwargs})

        if self._response_index < len(self.responses):
            response = self.responses[self._response_index]
            self._response_index += 1
            return FakeResponse(
                response['status'], response.get('json'), response.get('text')
            )

        return FakeResponse(200, {})

    def assert_request_made(self, url: str, method: str = 'POST') -> None:
        assert any(
            r['url'] == url and r.get('method', 'POST') == method
            for r in self.requests
        ), f'No {method} request made to {url}'

    def get_last_request(self) -> dict[str, Any] | None:
        return self.requests[-1] if self.requests else None


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        json_data: dict | None = None,
        text_data: str | None = None,
    ):
        self.status_code = status_code
        self._json = json_data
        self._text = text_data or ''

    def json(self):
        if self._json is None:
            raise ValueError('No JSON data')
        return self._json

    @property
    def text(self):
        return self._text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f'HTTP {self.status_code}')
