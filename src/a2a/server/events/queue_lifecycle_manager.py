"""SQS queue lifecycle manager for per-instance ECS auto-scaling support."""

from __future__ import annotations

import json
import logging
import uuid

from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import aioboto3


logger = logging.getLogger(__name__)


@dataclass
class QueueProvisionResult:
    """Result of a successful :meth:`QueueLifecycleManager.provision` call.

    Attributes:
        queue_url: The SQS queue URL.
        queue_arn: The SQS queue ARN.
        subscription_arn: The SNS subscription ARN.
    """

    queue_url: str
    queue_arn: str
    subscription_arn: str


@dataclass
class QueueLifecycleManager:
    """Manages the lifecycle of a per-instance SQS queue for ECS auto-scaling.

    Each ECS task instance calls :meth:`provision` on startup to create a
    dedicated SQS queue and subscribe it to the shared SNS topic.  On
    shutdown, :meth:`teardown` unsubscribes from SNS and deletes the queue.

    The manager also implements the async context-manager protocol so it can
    be used with ``async with``::

        async with QueueLifecycleManager(topic_arn='arn:aws:sns:...') as mgr:
            queue_url = mgr.queue_url
            # server starts here

    Requires the ``[aws]`` optional extra::

        pip install "a2a-sdk[aws]"

    Args:
        topic_arn: ARN of the SNS topic to subscribe the queue to.
        queue_name_prefix: Prefix for the generated SQS queue name.
            Defaults to ``'a2a-instance'``.
        instance_id: Unique ID for this instance. If *None*, a random UUID
            is generated automatically.
        region_name: AWS region (e.g. ``'us-east-1'``).
        session: Optional pre-created ``aioboto3.Session``. If *None*, a
            new default session is created on first use.
    """

    topic_arn: str
    queue_name_prefix: str = 'a2a-instance'
    instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    region_name: str = 'us-east-1'
    session: aioboto3.Session | None = field(default=None, repr=False)

    # Set after provision(); read via properties.
    _provision_result: QueueProvisionResult | None = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self) -> None:
        """Validates dependencies and lazily imports aioboto3."""
        try:
            import aioboto3  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                'To use QueueLifecycleManager, install the aws extra: '
                'pip install "a2a-sdk[aws]"'
            ) from exc

        if self.session is None:
            self.session = aioboto3.Session()

        logger.debug(
            'QueueLifecycleManager created (instance_id=%s, topic=%s).',
            self.instance_id,
            self.topic_arn,
        )

    # ------------------------------------------------------------------
    # Public properties (available after provision())
    # ------------------------------------------------------------------

    @property
    def queue_url(self) -> str | None:
        """SQS queue URL, or ``None`` if :meth:`provision` has not been called."""
        return (
            self._provision_result.queue_url if self._provision_result else None
        )

    @property
    def queue_arn(self) -> str | None:
        """SQS queue ARN, or ``None`` if :meth:`provision` has not been called."""
        return (
            self._provision_result.queue_arn if self._provision_result else None
        )

    @property
    def subscription_arn(self) -> str | None:
        """SNS subscription ARN, or ``None`` before :meth:`provision`."""
        return (
            self._provision_result.subscription_arn
            if self._provision_result
            else None
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def provision(self) -> QueueProvisionResult:
        """Creates a per-instance SQS queue and subscribes it to the SNS topic.

        The method is idempotent: calling it a second time simply returns the
        existing :class:`QueueProvisionResult` without creating new resources.

        Returns:
            A :class:`QueueProvisionResult` with the queue URL, ARN, and
            subscription ARN.

        Raises:
            Exception: Propagates AWS API errors. On SNS subscribe failure,
                the SQS queue is deleted as a rollback.
        """
        if self._provision_result is not None:
            logger.debug(
                'provision() called again — returning cached result '
                '(instance_id=%s).',
                self.instance_id,
            )
            return self._provision_result

        queue_name = f'{self.queue_name_prefix}-{self.instance_id}'
        logger.info(
            'Provisioning SQS queue %s in region %s.',
            queue_name,
            self.region_name,
        )

        async with self.session.client(
            'sqs', region_name=self.region_name
        ) as sqs:
            # Step 1: Create the SQS queue.
            create_resp = await sqs.create_queue(QueueName=queue_name)
            queue_url: str = create_resp['QueueUrl']
            logger.debug('SQS queue created: %s.', queue_url)

            # Step 2: Fetch the queue ARN.
            attr_resp = await sqs.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=['QueueArn'],
            )
            queue_arn: str = attr_resp['Attributes']['QueueArn']
            logger.debug('SQS queue ARN: %s.', queue_arn)

            # Step 3: Attach SNS → SQS access policy.  ARN values are
            # set as dict entries so json.dumps() escapes them safely,
            # preventing any injection via crafted ARN strings.
            policy = json.dumps(
                {
                    'Version': '2012-10-17',
                    'Statement': [
                        {
                            'Effect': 'Allow',
                            'Principal': {'Service': 'sns.amazonaws.com'},
                            'Action': 'SQS:SendMessage',
                            'Resource': queue_arn,
                            'Condition': {
                                'ArnEquals': {
                                    'aws:SourceArn': self.topic_arn,
                                },
                            },
                        }
                    ],
                }
            )
            await sqs.set_queue_attributes(
                QueueUrl=queue_url,
                Attributes={'Policy': policy},
            )
            logger.debug('SQS queue policy set for SNS delivery.')

        # Step 4: Subscribe the SQS queue to the SNS topic.
        subscription_arn = ''
        try:
            async with self.session.client(
                'sns', region_name=self.region_name
            ) as sns:
                sub_resp = await sns.subscribe(
                    TopicArn=self.topic_arn,
                    Protocol='sqs',
                    Endpoint=queue_arn,
                    Attributes={'RawMessageDelivery': 'true'},
                )
                subscription_arn = sub_resp['SubscriptionArn']
                logger.info('SNS subscription created: %s.', subscription_arn)
        except Exception:
            # Rollback: delete the queue we just created.
            logger.exception(
                'Failed to subscribe SQS queue to SNS. Rolling back queue %s.',
                queue_url,
            )
            try:
                async with self.session.client(
                    'sqs', region_name=self.region_name
                ) as sqs:
                    await sqs.delete_queue(QueueUrl=queue_url)
                    logger.debug('Rollback: SQS queue %s deleted.', queue_url)
            except Exception:
                logger.exception(
                    'Rollback failed: could not delete queue %s.', queue_url
                )
            raise

        self._provision_result = QueueProvisionResult(
            queue_url=queue_url,
            queue_arn=queue_arn,
            subscription_arn=subscription_arn,
        )
        return self._provision_result

    async def teardown(self) -> None:
        """Unsubscribes from SNS and deletes the SQS queue.

        Both operations are attempted even if the first one fails. Errors
        during teardown are logged but not re-raised, ensuring the operation
        is best-effort so the process can still exit cleanly.

        After teardown, :attr:`queue_url` and related properties return
        ``None``.
        """
        if self._provision_result is None:
            logger.debug(
                'teardown() called before provision() — nothing to do '
                '(instance_id=%s).',
                self.instance_id,
            )
            return

        result = self._provision_result
        self._provision_result = None

        # Step 1: Unsubscribe from SNS (best-effort).
        try:
            async with self.session.client(
                'sns', region_name=self.region_name
            ) as sns:
                await sns.unsubscribe(SubscriptionArn=result.subscription_arn)
                logger.info(
                    'SNS subscription %s removed.', result.subscription_arn
                )
        except Exception:
            logger.exception(
                'Failed to unsubscribe %s — continuing teardown.',
                result.subscription_arn,
            )

        # Step 2: Delete the SQS queue.
        try:
            async with self.session.client(
                'sqs', region_name=self.region_name
            ) as sqs:
                await sqs.delete_queue(QueueUrl=result.queue_url)
                logger.info('SQS queue %s deleted.', result.queue_url)
        except Exception:
            logger.exception('Failed to delete SQS queue %s.', result.queue_url)

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> QueueLifecycleManager:  # noqa: PYI034
        """Provisions resources and returns ``self``."""
        await self.provision()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Tears down resources on context exit."""
        await self.teardown()
