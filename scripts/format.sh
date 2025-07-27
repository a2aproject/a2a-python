#!/bin/bash
set -e
set -o pipefail

# --- Argument Parsing ---
# Initialize flags
FORMAT_ALL=false
RUFF_UNSAFE_FIXES_FLAG=""

# Process command-line arguments
# We use a while loop with shift to process each argument
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --all)
            FORMAT_ALL=true
            echo "Detected --all flag: Formatting all Python files."
            shift # Consume the argument
            ;;
        --unsafe-fixes)
            RUFF_UNSAFE_FIXES_FLAG="--unsafe-fixes"
            echo "Detected --unsafe-fixes flag: Ruff will run with unsafe fixes."
            shift # Consume the argument
            ;;
        *)
            # Handle unknown arguments or just ignore them if we only care about specific ones
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
    echo "Formatting all Python files in the repository."
    # Find all Python files, excluding grpc generated files as per original logic.
    # `sort -u` ensures unique files and consistent ordering for display/xargs.
    CHANGED_FILES=$(find . -name '*.py' -not -path './src/a2a/grpc/*' | sort -u)

    if [ -z "$CHANGED_FILES" ]; then
        echo "No Python files found to format."
        exit 0
    fi
else
    echo "No '--all' flag found. Formatting changed Python files based on git diff."
    TARGET_BRANCH="origin/${GITHUB_BASE_REF:-main}"
    git fetch origin "${GITHUB_BASE_REF:-main}" --depth=1

    MERGE_BASE=$(git merge-base HEAD "$TARGET_BRANCH")

    # Get python files changed in this PR, excluding grpc generated files
    CHANGED_FILES=$(git diff --name-only --diff-filter=ACMRTUXB "$MERGE_BASE" HEAD -- '*.py' ':!src/a2a/grpc/*')

    if [ -z "$CHANGED_FILES" ]; then
        echo "No changed Python files to format."
        exit 0
    fi
fi

echo "Files to be formatted:"
echo "$CHANGED_FILES"

# Helper function to run formatters with the list of files.
# The list of files is passed to xargs via stdin.
run_formatter() {
    echo "$CHANGED_FILES" | xargs -r "$@"
}

echo "Running pyupgrade..."
run_formatter pyupgrade --exit-zero-even-if-changed --py310-plus
echo "Running autoflake..."
run_formatter autoflake -i -r --remove-all-unused-imports
echo "Running ruff check (fix-only)..."
run_formatter ruff check --fix-only $RUFF_UNSAFE_FIXES_FLAG
echo "Running ruff format..."
run_formatter ruff format

echo "Formatting complete."
