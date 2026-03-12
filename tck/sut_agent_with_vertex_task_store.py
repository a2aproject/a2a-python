import os

import sut_agent


try:
    import vertexai
except ImportError as e:
    raise ImportError(
        'VertexTaskStore requires vertexai. '
        'Install with: '
        "'pip install a2a-sdk[vertex]'"
    ) from e

from a2a.contrib.tasks.vertex_task_store import VertexTaskStore


def main() -> None:
    """Main entrypoint."""
    project = os.environ.get('VERTEX_PROJECT')
    location = os.environ.get('VERTEX_LOCATION')
    base_url = os.environ.get('VERTEX_BASE_URL')
    api_version = os.environ.get('VERTEX_API_VERSION')
    agent_engine_resource_id = os.environ.get('AGENT_ENGINE_RESOURCE_ID')

    if (
        not project
        or not location
        or not base_url
        or not api_version
        or not agent_engine_resource_id
    ):
        raise ValueError(
            'Environment variables VERTEX_PROJECT, VERTEX_LOCATION, '
            'VERTEX_BASE_URL, VERTEX_API_VERSION, and '
            'AGENT_ENGINE_RESOURCE_ID must be defined'
        )

    client = vertexai.Client(
        project=project,
        location=location,
        http_options={'base_url': base_url, 'api_version': api_version},
    )

    sut_agent.serve(
        VertexTaskStore(
            client=client,
            agent_engine_resource_id=agent_engine_resource_id,
        )
    )


if __name__ == '__main__':
    main()
