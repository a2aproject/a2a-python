#!/bin/bash
set -e

for var in VERTEX_PROJECT VERTEX_LOCATION VERTEX_BASE_URL VERTEX_API_VERSION; do
  if [ -z "${!var}" ]; then
    echo "Error: Environment variable $var is undefined or empty." >&2
    exit 1
  fi
done

# Get the directory of this script
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTEST_ARGS=("$@")

uv run pytest -v "${PYTEST_ARGS[@]}" tests/contrib/tasks/test_vertex_task_store.py tests/contrib/tasks/test_vertex_task_converter.py
