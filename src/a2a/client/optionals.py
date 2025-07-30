from typing import TYPE_CHECKING


# Attempt to import the optional module
try:
    from grpc.aio import Channel  # pyright: ignore[reportAssignmentType]
except ImportError:
    # If grpc.aio is not available, define a dummy type for type checking.
    # This dummy type will only be used by type checkers.
    if TYPE_CHECKING:

        class Channel:  # type: ignore[no-redef]
            """Dummy class for type hinting when grpc.aio is not available."""

    else:
        Channel = None  # At runtime, pd will be None if the import failed.
