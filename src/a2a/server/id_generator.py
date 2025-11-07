import uuid

from abc import ABC, abstractmethod

from pydantic import BaseModel


class IDGeneratorContext(BaseModel):
    """Context for providing additional information to ID generators."""

    task_id: str | None = None
    context_id: str | None = None


class IDGenerator(ABC):
    """Interface for generating unique identifiers."""

    @abstractmethod
    def generate(self, context: IDGeneratorContext) -> str:
        pass


class UUIDGenerator(IDGenerator):
    """UUID implementation of the IDGenerator interface."""

    def generate(self, context: IDGeneratorContext) -> str:
        """Generates a random UUID, ignoring the context."""
        return str(uuid.uuid4())
