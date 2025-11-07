#!/bin/bash
#
# Generate Pydantic types from protobuf via JSON Schema.
#
# This script generates Python types using a 3-step pipeline:
# 1. buf generate - Generate Python protobuf files from a2a.proto
# 2. proto_to_json_schema.py - Convert proto descriptors to JSON Schema
# 3. datamodel-codegen - Generate Pydantic models from JSON Schema
#
# This approach uses protobuf as the source of truth while generating
# types with the same structure as the original JSON Schema-based types.
#

# Exit immediately if a command exits with a non-zero status.
# Treat unset variables as an error.
set -euo pipefail

TEMP_DIR=$(mktemp -d)
JSON_SCHEMA_FILE="$TEMP_DIR/a2a.json"

GENERATED_FILE=""

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    *)
      GENERATED_FILE="$1"
      shift 1
      ;;
  esac
done

if [ -z "$GENERATED_FILE" ]; then
  echo "Error: Output file path must be provided." >&2
  echo "Usage: $0 <output-file-path>"
  exit 1
fi

echo "Step 1: Generating protobuf Python files from proto..."
# First ensure we have the latest protobuf files
buf generate

# Run post-processor for gRPC files
uv run scripts/grpc_gen_post_processor.py

echo "Step 2: Converting protobuf descriptors to JSON Schema..."
echo "  - Temp JSON Schema: $JSON_SCHEMA_FILE"

# Convert proto descriptors to JSON Schema
uv run python scripts/proto_to_json_schema.py "$JSON_SCHEMA_FILE"

echo "Step 3: Generating Pydantic models from JSON Schema..."
echo "  - Source JSON: $JSON_SCHEMA_FILE"
echo "  - Output File: $GENERATED_FILE"

# Use the existing JSON Schema generator
uv run datamodel-codegen \
  --input "$JSON_SCHEMA_FILE" \
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

# Cleanup
rm -rf "$TEMP_DIR"
