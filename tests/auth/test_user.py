import unittest

from inspect import isabstract

from a2a.auth.user import UnauthenticatedUser, User


class TestUser(unittest.TestCase):
    def test_is_abstract(self):
        self.assertTrue(isabstract(User))


class TestUnauthenticatedUser(unittest.TestCase):
    def test_is_user_subclass(self):
        self.assertTrue(issubclass(UnauthenticatedUser, User))

    def test_is_authenticated_returns_false(self):
        user = UnauthenticatedUser()
        self.assertFalse(user.is_authenticated)

    def test_user_name_returns_empty_string(self):
        user = UnauthenticatedUser()
        self.assertEqual(user.user_name, '')


if __name__ == '__main__':
    unittest.main()
