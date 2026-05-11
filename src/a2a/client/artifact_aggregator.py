from collections.abc import AsyncIterator

from a2a.types import Artifact, StreamResponse


class ArtifactsAggregator:
    """Client-side utility for assembling Artifact objects from a stream of StreamResponse events.

    Interprets the append and last_chunk flags of TaskArtifactUpdateEvent to
    reconstruct complete artifacts from chunked streaming responses. Each instance
    wraps a single stream that can be consumed only once.
    """

    def __init__(self, stream: AsyncIterator[StreamResponse]) -> None:
        self._stream = stream

    @classmethod
    def from_stream(
        cls, stream: AsyncIterator[StreamResponse]
    ) -> 'ArtifactsAggregator':
        """Create an ArtifactsAggregator from an async stream of StreamResponse events.

        Args:
            stream: An async iterator of StreamResponse objects, typically obtained
                from BaseClient.send_message.

        Returns:
            A new ArtifactsAggregator instance.
        """
        return cls(stream)

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        """Assemble and return a single Artifact by its ID from the stream.

        Iterates over the stream and collects all parts belonging to the artifact
        with the given ID, stopping when last_chunk is True.

        Args:
            artifact_id: The ID of the artifact to assemble.

        Returns:
            The assembled Artifact with all collected parts,
            or None if the artifact_id was not found in the stream.

        Note:
            Consumes the stream. Do not call this method and get_all_artifacts
            on the same instance.
        """
        artifact = None

        async for event in self._stream:
            if not event.HasField('artifact_update'):
                continue

            if event.artifact_update.artifact.artifact_id == artifact_id:
                if artifact is None or not event.artifact_update.append:
                    artifact = Artifact()
                    artifact.CopyFrom(event.artifact_update.artifact)
                else:
                    artifact.parts.extend(event.artifact_update.artifact.parts)

                if event.artifact_update.last_chunk:
                    break
        return artifact

    async def get_all_artifacts(self) -> list[Artifact]:
        """Assemble and return all Artifacts from the stream.

        Iterates over the entire stream and assembles all artifacts, handling
        interleaved chunks from multiple artifacts using artifact_id as the key.
        If append is False, the parts for that artifact are reset before adding
        the new parts.

        Returns:
            A list of assembled Artifact objects.

        Note:
            Consumes the stream. Do not call this method and get_artifact
            on the same instance.
        """
        artifacts: dict[str, Artifact] = {}

        async for event in self._stream:
            if not event.HasField('artifact_update'):
                continue

            artifact_id = event.artifact_update.artifact.artifact_id

            if artifact_id not in artifacts or not event.artifact_update.append:
                artifacts[artifact_id] = Artifact()
                artifacts[artifact_id].CopyFrom(event.artifact_update.artifact)
            else:
                artifacts[artifact_id].parts.extend(
                    event.artifact_update.artifact.parts
                )
        return list(artifacts.values())
