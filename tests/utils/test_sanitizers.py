"""Tests for path parameter sanitization utilities."""

import pytest

from a2a.utils.errors import InvalidRequestError
from a2a.utils.sanitizers import sanitize_path_id


class TestSanitizePathId:
    """Tests for sanitize_path_id validation."""

    def test_valid_alphanumeric(self) -> None:
        """Alphanumeric IDs pass validation unchanged."""
        assert sanitize_path_id('abc123') == 'abc123'

    def test_valid_uuid(self) -> None:
        """UUID-style IDs pass validation."""
        uid = '550e8400-e29b-41d4-a716-446655440000'
        assert sanitize_path_id(uid) == uid

    def test_valid_with_underscores(self) -> None:
        """IDs with underscores pass validation."""
        assert sanitize_path_id('task_123') == 'task_123'

    def test_valid_with_dots(self) -> None:
        """IDs with dots pass validation."""
        assert sanitize_path_id('v1.0.3') == 'v1.0.3'

    def test_valid_with_hyphens(self) -> None:
        """IDs with hyphens pass validation."""
        assert sanitize_path_id('my-task-id') == 'my-task-id'

    def test_empty_string_rejected(self) -> None:
        """Empty string raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='must not be empty'):
            sanitize_path_id('')

    def test_null_byte_rejected(self) -> None:
        """Null byte raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='control characters'):
            sanitize_path_id('abc\x00def')

    def test_newline_rejected(self) -> None:
        """Newline raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='control characters'):
            sanitize_path_id('abc\ndef')

    def test_carriage_return_rejected(self) -> None:
        """Carriage return raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='control characters'):
            sanitize_path_id('abc\rdef')

    def test_tab_rejected(self) -> None:
        """Tab raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='control characters'):
            sanitize_path_id('abc\tdef')

    def test_del_control_char_rejected(self) -> None:
        """DEL (0x7F) raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='control characters'):
            sanitize_path_id('abc\x7f')

    def test_space_rejected(self) -> None:
        """Space raises InvalidRequestError (not in allowed set)."""
        with pytest.raises(InvalidRequestError, match='invalid characters'):
            sanitize_path_id('abc def')

    def test_slash_rejected(self) -> None:
        """Forward slash raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='invalid characters'):
            sanitize_path_id('abc/def')

    def test_backslash_rejected(self) -> None:
        """Backslash raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='invalid characters'):
            sanitize_path_id('abc\\def')

    def test_dot_rejected(self) -> None:
        """Single dot is rejected as a path traversal risk."""
        with pytest.raises(InvalidRequestError, match='cannot be "." or ".."'):
            sanitize_path_id('.')

    def test_double_dot_rejected(self) -> None:
        """Double dot is rejected as a path traversal risk."""
        with pytest.raises(InvalidRequestError, match='cannot be "." or ".."'):
            sanitize_path_id('..')

    def test_path_traversal_rejected(self) -> None:
        """Path traversal sequence raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='invalid characters'):
            sanitize_path_id('../../etc/passwd')

    def test_custom_param_name_in_error(self) -> None:
        """Custom param_name appears in error messages."""
        with pytest.raises(InvalidRequestError, match='push_id'):
            sanitize_path_id('', 'push_id')

    def test_unicode_rejected(self) -> None:
        """Non-ASCII unicode characters raise InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='invalid characters'):
            sanitize_path_id('task-你好')

    def test_question_mark_rejected(self) -> None:
        """Question mark raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='invalid characters'):
            sanitize_path_id('abc?def')

    def test_hash_rejected(self) -> None:
        """Hash character raises InvalidRequestError."""
        with pytest.raises(InvalidRequestError, match='invalid characters'):
            sanitize_path_id('abc#def')
