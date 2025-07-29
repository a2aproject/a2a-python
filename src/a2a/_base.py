import warnings

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


def to_camel_custom(snake: str) -> str:
    """Convert a snake_case string to camelCase.

    Args:
        snake: The string to convert.

    Returns:
        The converted camelCase string.
    """
    # First, remove any trailing underscores. This is common for names that
    # conflict with Python keywords, like 'in_' or 'from_'.
    if snake.endswith('_'):
        snake = snake.rstrip('_')
    return to_camel(snake)


class A2ABaseModel(BaseModel):
    """Base class for shared behavior across A2A data models.

    Provides a common configuration (e.g., alias-based population) and
    serves as the foundation for future extensions or shared utilities.

    This implementation provides backward compatibility for camelCase aliases
    by lazy-loading an alias map upon first use. Accessing or setting
    attributes via their camelCase alias will raise a DeprecationWarning.
    """

    model_config = ConfigDict(
        # SEE: https://docs.pydantic.dev/latest/api/config/#pydantic.config.ConfigDict.populate_by_name
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        alias_generator=to_camel_custom,
    )

    # Cache for the alias -> field_name mapping.
    # It starts as None and is populated on first access.
    _alias_to_field_name_map: ClassVar[dict[str, str] | None] = None

    @classmethod
    def _get_alias_map(cls) -> dict[str, str]:
        """Lazily builds and returns the alias-to-field-name mapping for the class.

        The map is cached on the class object to avoid re-computation.
        """
        if cls._alias_to_field_name_map is None:
            cls._alias_to_field_name_map = {
                field.alias: field_name
                for field_name, field in cls.model_fields.items()
                if field.alias is not None
            }
        return cls._alias_to_field_name_map

    def __setattr__(self, name: str, value: Any) -> None:
        """Allow setting attributes via their camelCase alias."""
        # Get the map and find the corresponding snake_case field name.
        field_name = type(self)._get_alias_map().get(name)  # noqa: SLF001

        if field_name and field_name != name:
            # An alias was used, issue a warning.
            warnings.warn(
                (
                    f"Setting field '{name}' via its camelCase alias is deprecated and will be removed in version 0.3.0 "
                    f"Use the snake_case name '{field_name}' instead."
                ),
                DeprecationWarning,
                stacklevel=2,
            )

        # If an alias was used, field_name will be set; otherwise, use the original name.
        super().__setattr__(field_name or name, value)

    def __getattr__(self, name: str) -> Any:
        """Allow getting attributes via their camelCase alias."""
        # Get the map and find the corresponding snake_case field name.
        field_name = type(self)._get_alias_map().get(name)  # noqa: SLF001

        if field_name and field_name != name:
            # An alias was used, issue a warning.
            warnings.warn(
                (
                    f"Accessing field '{name}' via its camelCase alias is deprecated and will be removed in version 0.3.0 "
                    f"Use the snake_case name '{field_name}' instead."
                ),
                DeprecationWarning,
                stacklevel=2,
            )

            # If an alias was used, retrieve the actual snake_case attribute.
            return getattr(self, field_name)

        # If it's not a known alias, it's a genuine missing attribute.
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )
