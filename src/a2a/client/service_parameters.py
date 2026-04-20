from collections.abc import Callable
from typing import TypeAlias

from a2a.extensions.common import (
    HTTP_EXTENSION_HEADER,
    get_requested_extensions,
)


ServiceParameters: TypeAlias = dict[str, str]
ServiceParametersUpdate: TypeAlias = Callable[[ServiceParameters], None]


class ServiceParametersFactory:
    """Factory for creating ServiceParameters."""

    @staticmethod
    def create(updates: list[ServiceParametersUpdate]) -> ServiceParameters:
        """Create ServiceParameters from a list of updates.

        Args:
            updates: List of update functions to apply.

        Returns:
            The created ServiceParameters dictionary.
        """
        return ServiceParametersFactory.create_from(None, updates)

    @staticmethod
    def create_from(
        service_parameters: ServiceParameters | None,
        updates: list[ServiceParametersUpdate],
    ) -> ServiceParameters:
        """Create new ServiceParameters from existing ones and apply updates.

        Args:
            service_parameters: Optional existing ServiceParameters to start from.
            updates: List of update functions to apply.

        Returns:
            New ServiceParameters dictionary.
        """
        result = service_parameters.copy() if service_parameters else {}
        for update in updates:
            update(result)
        return result


def with_a2a_extensions(extensions: list[str]) -> ServiceParametersUpdate:
    """Create a ServiceParametersUpdate that merges A2A extension URIs.

    Unions the supplied URIs with any already present in the A2A-Extensions
    parameter, deduplicating and emitting them in sorted order. Repeated
    calls accumulate rather than overwrite.
    """

    def update(parameters: ServiceParameters) -> None:
        if not extensions:
            return
        existing = parameters.get(HTTP_EXTENSION_HEADER, '')
        merged = sorted(get_requested_extensions([existing, *extensions]))
        parameters[HTTP_EXTENSION_HEADER] = ','.join(merged)

    return update
