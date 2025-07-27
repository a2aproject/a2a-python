#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
# Treat unset variables as an error.
set -euo pipefail

# Check if an output file path was provided as an argument.
if [ -z "$1" ]; then
  echo "Error: Output file path must be provided as the first argument." >&2
  exit 1
fi

REMOTE_URL="https://raw.githubusercontent.com/a2aproject/A2A/refs/heads/main/specification/json/a2a.json"
GENERATED_FILE="$1"

echo "Running datamodel-codegen..."
echo "  - Source URL: $REMOTE_URL"
echo "  - Output File: $GENERATED_FILE"

uv run datamodel-codegen \
  --url "$REMOTE_URL" \
  --input-file-type jsonschema \
  --output "$GENERATED_FILE" \
  --target-python-version 3.10 \
  --output-model-type pydantic_v2.BaseModel \
  --disable-timestamp \
  --use-schema-description \
  --use-union-operator \
  --use-field-description \
  --use-default \
  --use-default-kwarg \
  --use-one-literal-as-default \
  --class-name A2A \
  --use-standard-collections \
  --use-subclass-enum \
  --base-class a2a._base.A2ABaseModel \
  --field-constraints \
  --snake-case-field \
  --no-alias

echo "Formatting generated file with ruff..."
uv run ruff format "$GENERATED_FILE"

echo "Codegen finished successfully."
