from a2a.auth.user import User

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope


class SampleUser(User):
    """A test implementation of the User interface."""

    def __init__(self, user_name: str):
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


def test_resolve_user_scope_valid_user():
    """Test resolve_user_scope with a valid user in the context."""
    user = SampleUser(user_name='SampleUser')
    context = ServerCallContext(user=user)
    assert resolve_user_scope(context) == 'SampleUser'


def test_resolve_user_scope_no_context():
    """Test resolve_user_scope when the context is None."""
    assert resolve_user_scope(None) == 'unknown'
