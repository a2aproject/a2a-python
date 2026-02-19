from collections.abc import Callable

from a2a.server.context import ServerCallContext


# Definition
OwnerResolver = Callable[[ServerCallContext], str]


# Example Default Implementation
def resolve_user_scope(context: ServerCallContext | None) -> str:
    """Resolves the owner scope based on the user in the context."""
    if not context:
        return 'unknown'
    if not context.user:
        raise ValueError('User not found in context.')
    # Example: Basic user name. Adapt as needed for your user model.
    return context.user.user_name
