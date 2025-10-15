#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
# Treat unset variables as an error.
set -euo pipefail

A2A_SPEC_REPO="https://github.com/a2aproject/A2A.git" # URL for the A2A spec repo.
A2A_SPEC_BRANCH="main" # Name of the branch with experimental changes.
FEATURE_BRANCH="experimental-types" # Name of the feature branch to create.
ROOT_DIR=$(git rev-parse --show-toplevel)

usage() {
  cat <<EOF
Usage: $0 [OPTIONS]

Creates a new feature branch with types generated from unmerged A2A spec changes.

This script clones the A2A spec repository, checks out a specific branch,
and creates a new local feature branch from it.

OPTIONS:
  -r, --spec-repo       URL for the A2A spec repository.
                        (Default: "$A2A_SPEC_REPO")

  -b, --spec-branch     Name of the branch with the experimental changes.
                        (Default: "$A2A_SPEC_BRANCH")

  -f, --feature-branch  Name of the new feature branch to create.
                        (Default: "$FEATURE_BRANCH")

  -h, --help            Display this help message and exit.

EXAMPLE:
  # Run with all default settings:
  $0

  # Run with custom settings:
  $0  -r "https://github.com/spec-fork/A2A.git" -b "spec-change" -f "my-branch"
EOF
}

# Handle command-line arguments.
while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--help)
      usage
      exit 0
      ;;
    -r|--spec-repo)
      A2A_SPEC_REPO="$2"
      shift 2
      ;;
    -b|--spec-branch)
      A2A_SPEC_BRANCH="$2"
      shift 2
      ;;
    -f|--feature-branch)
      FEATURE_BRANCH="$2"
      shift 2
      ;;
    -t|--tmp-dir)
      TMP_WORK_DIR="$2"
      shift 2
      ;;
    *)
      echo "Error: Unknown option '$1'" >&2
      usage
      exit 1
      ;;
  esac
done

TMP_WORK_DIR=$(mktemp -d)
echo "Created a temporary working directory: $TMP_WORK_DIR"
trap 'rm -rf -- "$TMP_WORK_DIR"' EXIT
cd $TMP_WORK_DIR

echo "Cloning the \"$A2A_SPEC_REPO\" repository..."
git clone $A2A_SPEC_REPO spec_repo
cd spec_repo

echo "Checking out the \"$A2A_SPEC_BRANCH\" branch..."
git checkout $A2A_SPEC_BRANCH

echo "Running datamodel-codegen..."
GENERATED_FILE="$ROOT_DIR/src/a2a/types.py"
uv run datamodel-codegen \
  --input "$TMP_WORK_DIR/spec_repo/specification/json/a2a.json" \
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

echo "Formatting generated types file with ruff..."
uv run ruff format "$GENERATED_FILE"

echo "Committing generated types file to the \"$FEATURE_BRANCH\" branch..."
cd $ROOT_DIR
git checkout -b "$FEATURE_BRANCH"
git add "$GENERATED_FILE"
git commit -m "Experimental types"
