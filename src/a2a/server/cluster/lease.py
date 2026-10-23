from abc import ABC, abstractmethod
from dataclasses import dataclass

from a2a.server.context import ServerCallContext


DEFAULT_LEASE_TTL = 45.0
DEFAULT_HEARTBEAT_INTERVAL = 10.0


@dataclass(frozen=True)
class TaskLease:
    """A held turn lease. Only the holder's `token` can renew or release it."""

    task_id: str
    owner: str
    token: str


class TaskLeaseStore(ABC):
    """Records which replica is running a turn for each task.

    The replica running a turn holds the task's lease and renews it while
    the turn runs. A lease that expires belongs to a replica that stopped
    renewing it, so its task can be recovered (see `fail_abandoned_tasks`).
    """

    @abstractmethod
    async def acquire(
        self, task_id: str, context: ServerCallContext, ttl: float
    ) -> TaskLease | None:
        """Takes the lease for `ttl` seconds.

        Returns None if another holder's lease has not expired. An expired
        lease is taken over.
        """

    @abstractmethod
    async def renew(self, lease: TaskLease, ttl: float) -> bool:
        """Extends `lease` by `ttl` seconds.

        Returns False if the lease is no longer held with this token. Must
        never recreate a released or taken-over lease.
        """

    @abstractmethod
    async def release(self, lease: TaskLease) -> None:
        """Drops `lease` if it is still held with its token."""

    @abstractmethod
    async def claim_expired(self, ttl: float, limit: int) -> list[TaskLease]:
        """Claims up to `limit` expired leases for recovery.

        Each claimed lease gets a new token and a new expiry `ttl` seconds
        away, so concurrent callers never claim the same lease.
        """
