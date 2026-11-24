"""The `experimental` marker."""

from a2a.utils.experimental import experimental


def test_marks_a_class_and_keeps_its_docstring():
    """The class keeps its docstring, gains the note and is flagged."""

    @experimental
    class Feature:
        """Does a thing."""

    assert Feature.__doc__.startswith('Does a thing.')
    assert 'Experimental' in Feature.__doc__
    assert Feature.__a2a_experimental__ is True


def test_marks_a_function_without_a_docstring():
    """A function with no docstring gets the note and still works."""

    @experimental
    def compute():
        return 42

    assert compute() == 42
    assert 'Experimental' in compute.__doc__
    assert compute.__a2a_experimental__ is True
