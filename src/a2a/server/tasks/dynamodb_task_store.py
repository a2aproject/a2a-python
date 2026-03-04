"""DynamoDB-backed implementation of TaskStore for persistent task storage."""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import aioboto3

    from a2a.server.context import ServerCallContext

from a2a.server.tasks.task_store import TaskStore
from a2a.types import Task


logger = logging.getLogger(__name__)


class DynamoDBTaskStore(TaskStore):
    """DynamoDB-backed implementation of TaskStore.

    Stores Task objects in an AWS DynamoDB table. Task data is serialized as
    JSON strings using Pydantic's ``model_dump_json`` / ``model_validate_json``.

    Requires the ``[aws]`` optional extra::

        pip install "a2a-sdk[aws]"

    Table schema:
        Partition key: ``task_id`` (String)
        Attribute:     ``task_data`` (String — JSON-serialized Task)

    Example:
        >>> import aioboto3
        >>> session = aioboto3.Session()
        >>> store = DynamoDBTaskStore('my-a2a-tasks', session=session)

    Args:
        table_name: Name of the DynamoDB table.
        region_name: AWS region (e.g. ``'us-east-1'``). Ignored when
            *session* is provided with an explicit region.
        session: Optional pre-created ``aioboto3.Session``. If *None*, a
            new default session is created on first use.
    """

    def __init__(
        self,
        table_name: str,
        *,
        region_name: str = 'us-east-1',
        session: aioboto3.Session | None = None,
    ) -> None:
        """Initializes the DynamoDBTaskStore."""
        try:
            import aioboto3  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                'To use DynamoDBTaskStore, install the aws extra: '
                'pip install "a2a-sdk[aws]"'
            ) from exc

        self._table_name = table_name
        self._region_name = region_name
        self._session = session or aioboto3.Session()
        logger.debug(
            'DynamoDBTaskStore initialised (table=%s, region=%s).',
            table_name,
            region_name,
        )

    async def save(
        self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Saves or updates a task in DynamoDB.

        Args:
            task: The Task object to persist.
            context: Optional server call context (unused).
        """
        logger.debug(
            'Saving task %s to DynamoDB table %s.', task.id, self._table_name
        )
        task_data = task.model_dump_json()
        async with self._session.client(  # pyright: ignore[reportGeneralTypeIssues]
            'dynamodb', region_name=self._region_name
        ) as client:
            await client.put_item(
                TableName=self._table_name,
                Item={
                    'task_id': {'S': task.id},
                    'task_data': {'S': task_data},
                },
            )
        logger.debug('Task %s saved to DynamoDB successfully.', task.id)

    async def get(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Retrieves a task from DynamoDB by ID.

        Args:
            task_id: The ID of the task to retrieve.
            context: Optional server call context (unused).

        Returns:
            The Task if found, otherwise ``None``.
        """
        logger.debug(
            'Getting task %s from DynamoDB table %s.',
            task_id,
            self._table_name,
        )
        async with self._session.client(  # pyright: ignore[reportGeneralTypeIssues]
            'dynamodb', region_name=self._region_name
        ) as client:
            response = await client.get_item(
                TableName=self._table_name,
                Key={'task_id': {'S': task_id}},
                ConsistentRead=True,
            )
        item = response.get('Item')
        if not item:
            logger.debug('Task %s not found in DynamoDB.', task_id)
            return None
        task_data = item['task_data']['S']
        task = Task.model_validate_json(task_data)
        logger.debug('Task %s retrieved from DynamoDB successfully.', task_id)
        return task

    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Deletes a task from DynamoDB by ID.

        DynamoDB's ``delete_item`` is idempotent — deleting a nonexistent
        task is a no-op.

        Args:
            task_id: The ID of the task to delete.
            context: Optional server call context (unused).
        """
        logger.debug(
            'Deleting task %s from DynamoDB table %s.',
            task_id,
            self._table_name,
        )
        async with self._session.client(  # pyright: ignore[reportGeneralTypeIssues]
            'dynamodb', region_name=self._region_name
        ) as client:
            await client.delete_item(
                TableName=self._table_name,
                Key={'task_id': {'S': task_id}},
            )
        logger.debug('Task %s deleted from DynamoDB successfully.', task_id)
