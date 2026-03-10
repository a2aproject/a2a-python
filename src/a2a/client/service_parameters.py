from collections.abc import Callable
from typing import TypeAlias

from a2a.extensions.common import HTTP_EXTENSION_HEADER


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
    """Create a ServiceParametersUpdate that adds A2A extensions.

    Args:
        extensions: List of extension strings.

    Returns:
        A function that updates ServiceParameters with the extensions header.
    """

    def update(parameters: ServiceParameters) -> None:
        if extensions:
            parameters[HTTP_EXTENSION_HEADER] = ','.join(extensions)

    return update
