#!/bin/bash
set -e
set -o pipefail

# --- Argument Parsing ---
# Initialize flags
FORMAT_ALL=false
RUFF_UNSAFE_FIXES_FLAG=""

# Process command-line arguments
while [[ "$#" -gt 0 ]]; do
    case "$1" in
    --all)
        FORMAT_ALL=true
        echo "Detected --all flag: Formatting all tracked Python files."
        shift # Consume the argument
        ;;
    --unsafe-fixes)
        RUFF_UNSAFE_FIXES_FLAG="--unsafe-fixes"
        echo "Detected --unsafe-fixes flag: Ruff will run with unsafe fixes."
        shift # Consume the argument
        ;;
    *)
        # Handle unknown arguments or just ignore them
        echo "Warning: Unknown argument '$1'. Ignoring."
        shift # Consume the argument
        ;;
    esac
done

# Sort Spelling Allowlist
SPELLING_ALLOW_FILE=".github/actions/spelling/allow.txt"
if [ -f "$SPELLING_ALLOW_FILE" ]; then
    echo "Sorting and de-duplicating $SPELLING_ALLOW_FILE"
    sort -u "$SPELLING_ALLOW_FILE" -o "$SPELLING_ALLOW_FILE"
fi

CHANGED_FILES=""

if $FORMAT_ALL; then
    echo "Finding all tracked Python files in the repository..."
    CHANGED_FILES=$(git ls-files -- '*.py' ':!src/a2a/grpc/*')
else
    echo "Finding changed Python files based on git diff..."
    TARGET_BRANCH="origin/${GITHUB_BASE_REF:-main}"
    git fetch origin "${GITHUB_BASE_REF:-main}" --depth=1

    MERGE_BASE=$(git merge-base HEAD "$TARGET_BRANCH")

    # Get python files changed in this PR, excluding grpc generated files.
    CHANGED_FILES=$(git diff --name-only --diff-filter=ACMRTUXB "$MERGE_BASE" HEAD -- '*.py' ':!src/a2a/grpc/*')
fi

# Exit if no files were found
if [ -z "$CHANGED_FILES" ]; then
    echo "No changed or tracked Python files to format."
    exit 0
fi

# --- Helper Function ---
# Runs a command on a list of files passed via stdin.
# $1: A string containing the list of files (space-separated).
# $2...: The command and its arguments to run.
run_formatter() {
    local files_to_format="$1"
    shift # Remove the file list from the arguments
    if [ -n "$files_to_format" ]; then
        echo "$files_to_format" | xargs -r "$@"
    fi
}

# --- Python File Formatting ---
if [ -n "$CHANGED_FILES" ]; then
    echo "--- Formatting Python Files ---"
    echo "Files to be formatted:"
    echo "$CHANGED_FILES"

    echo "Running autoflake..."
    run_formatter "$CHANGED_FILES" autoflake -i -r --remove-all-unused-imports
    echo "Running ruff check (fix-only)..."
    run_formatter "$CHANGED_FILES" ruff check --fix-only $RUFF_UNSAFE_FIXES_FLAG
    echo "Running ruff format..."
    run_formatter "$CHANGED_FILES" ruff format
    echo "Python formatting complete."
else
    echo "No Python files to format."
fi

echo "All formatting tasks are complete."
