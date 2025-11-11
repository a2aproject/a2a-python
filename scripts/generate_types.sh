#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
# Treat unset variables as an error.
set -euo pipefail

# A2A specification version to use
# Can be overridden via environment variable: A2A_SPEC_VERSION=v1.2.0 ./generate_types.sh
# Or via command-line flag: ./generate_types.sh --version v1.2.0 output.py
# Use a specific git tag, branch name, or commit SHA
# Examples: "v1.0.0", "v1.2.0", "main", "abc123def"
A2A_SPEC_VERSION="${A2A_SPEC_VERSION:-v0.3.0}"

# Build URL based on version format
# Tags use /refs/tags/, branches use /refs/heads/, commits use direct ref
build_remote_url() {
  local version="$1"
  local base_url="https://raw.githubusercontent.com/a2aproject/A2A"
  local spec_path="specification/json/a2a.json"
  local url_part

  if [[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    # Looks like a version tag (v1.0.0, v1.2.3)
    url_part="refs/tags/${version}"
  elif [[ "$version" =~ ^[0-9a-f]{7,40}$ ]]; then
    # Looks like a commit SHA (7+ hex chars)
    url_part="${version}"
  else
    # Assume it's a branch name (main, develop, etc.)
    url_part="refs/heads/${version}"
  fi
  echo "${base_url}/${url_part}/${spec_path}"
}

REMOTE_URL=$(build_remote_url "$A2A_SPEC_VERSION")

GENERATED_FILE=""
INPUT_FILE=""

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
  --input-file)
    INPUT_FILE="$2"
    shift 2
    ;;
  --version)
    A2A_SPEC_VERSION="$2"
    REMOTE_URL=$(build_remote_url "$A2A_SPEC_VERSION")
    shift 2
    ;;
  *)
    GENERATED_FILE="$1"
    shift 1
    ;;
  esac
done

if [ -z "$GENERATED_FILE" ]; then
  cat >&2 <<EOF
Error: Output file path must be provided.
Usage: $0 [--input-file <path>] [--version <version>] <output-file-path>
Options:
  --input-file <path>   Use a local JSON schema file instead of fetching from remote
  --version <version>   Specify A2A spec version (default: v0.3.0)
                        Can be a git tag (v1.0.0), branch (main), or commit SHA
Environment variables:
  A2A_SPEC_VERSION      Override default spec version
Examples:
  $0 src/a2a/types.py
  $0 --version v1.2.0 src/a2a/types.py
  $0 --input-file local/a2a.json src/a2a/types.py
  A2A_SPEC_VERSION=main $0 src/a2a/types.py
EOF
  exit 1
fi

echo "Running datamodel-codegen..."
declare -a source_args
if [ -n "$INPUT_FILE" ]; then
  echo "  - Source File: $INPUT_FILE"
  if [ ! -f "$INPUT_FILE" ]; then
    echo "Error: Input file does not exist: $INPUT_FILE" >&2
    exit 1
  fi
  source_args=("--input" "$INPUT_FILE")
else
  echo "  - A2A Spec Version: $A2A_SPEC_VERSION"
  echo "  - Source URL: $REMOTE_URL"

  # Validate that the remote URL is accessible
  echo "  - Validating remote URL..."
  if ! curl --fail --silent --head "$REMOTE_URL" >/dev/null 2>&1; then
    cat >&2 <<EOF

Error: Unable to access A2A specification at version '$A2A_SPEC_VERSION'
URL: $REMOTE_URL

The version may not exist. Available versions can be found at:
  https://github.com/a2aproject/A2A/tags

EOF
    exit 1
  fi

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
