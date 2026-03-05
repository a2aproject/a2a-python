#!/bin/bash
set -e

for var in VERTEX_PROJECT VERTEX_LOCATION VERTEX_BASE_URL VERTEX_API_VERSION; do
  if [ -z "${!var}" ]; then
    echo "Error: Environment variable $var is undefined or empty." >&2
    exit 1
  fi
done

PYTEST_ARGS=("$@")

echo "Running Vertex tests..."

cd $(git rev-parse --show-toplevel)

uv run pytest -v "${PYTEST_ARGS[@]}" tests/contrib/tasks/test_vertex_task_store.py tests/contrib/tasks/test_vertex_task_converter.py
