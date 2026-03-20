from collections.abc import Callable

from a2a.server.context import ServerCallContext


# Definition
OwnerResolver = Callable[[ServerCallContext], str]


# Example Default Implementation
def resolve_user_scope(context: ServerCallContext) -> str:
    """Resolves the owner scope based on the user in the context."""
    return context.user.user_name
