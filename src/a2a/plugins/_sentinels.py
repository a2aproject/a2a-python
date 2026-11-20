"""Internal sentinels shared by the SDK's plugin call sites."""

from __future__ import annotations


class StreamEnd:
    """Marker for a stream with no more events.

    Used instead of ``None`` because an interceptor can set a stream event to
    ``None``, which would otherwise look like the end of the stream.
    """


STREAM_END = StreamEnd()
