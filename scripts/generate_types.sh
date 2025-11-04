#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
# Treat unset variables as an error.
set -euo pipefail

REMOTE_URL="https://raw.githubusercontent.com/a2aproject/A2A/refs/heads/main/specification/json/a2a.json"

GENERATED_FILE=""
INPUT_FILE=""

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-file)
      INPUT_FILE="$2"
      shift 2
      ;;
    *)
      GENERATED_FILE="$1"
      shift 1
      ;;
  esac
done

if [ -z "$GENERATED_FILE" ]; then
  echo "Error: Output file path must be provided." >&2
  echo "Usage: $0 [--input-file <path>] <output-file-path>"
  exit 1
fi

echo "Running datamodel-codegen..."
declare -a source_args
if [ -n "$INPUT_FILE" ]; then
  echo "  - Source File: $INPUT_FILE"
  source_args=("--input" "$INPUT_FILE")
else
  echo "  - Source URL: $REMOTE_URL"
  source_args=("--url" "$REMOTE_URL")
fi
echo "  - Output File: $GENERATED_FILE"

uv run datamodel-codegen \
  "${source_args[@]}" \
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
