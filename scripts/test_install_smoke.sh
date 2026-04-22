#!/bin/bash
# Local equivalent of .github/workflows/install-smoke.yml.
#
# For each install profile, builds the wheel and installs it into a
# clean venv (no dev deps), then runs the import smoke test for that
# profile. By default runs every known profile; pass a profile name
# to run just one.
#
# Available profiles (must match those in scripts/test_install_smoke.py):
#   base         -- `pip install a2a-sdk`
#   http-server  -- `pip install a2a-sdk[http-server]`
#   grpc         -- `pip install a2a-sdk[grpc]`
#   telemetry    -- `pip install a2a-sdk[telemetry]`
#   sql          -- `pip install a2a-sdk[sql]`
#
# Usage:
#   scripts/test_install_smoke.sh [profile] [python-version]
#
# Examples:
#   scripts/test_install_smoke.sh                       # all profiles, default python
#   scripts/test_install_smoke.sh '' 3.13               # all profiles on python 3.13
#   scripts/test_install_smoke.sh http-server           # http-server only
#   scripts/test_install_smoke.sh http-server 3.13      # http-server on python 3.13
set -e
set -o pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

ALL_PROFILES=(base http-server grpc telemetry sql)

PROFILE_ARG="${1:-}"
PYTHON_VERSION="${2:-}"

if [ -z "$PROFILE_ARG" ]; then
    PROFILES=("${ALL_PROFILES[@]}")
else
    PROFILES=("$PROFILE_ARG")
fi

extras_for_profile() {
    case "$1" in
        base)        echo "" ;;
        http-server) echo "[http-server]" ;;
        grpc)        echo "[grpc]" ;;
        telemetry)   echo "[telemetry]" ;;
        sql)         echo "[sql]" ;;
        *)
            echo "Unknown profile '$1'. Available: ${ALL_PROFILES[*]}" >&2
            return 1
            ;;
    esac
}

# Validate profiles up-front so we fail fast.
for profile in "${PROFILES[@]}"; do
    extras_for_profile "$profile" >/dev/null
done

echo "--- Building wheel ---"
rm -rf dist
uv build --wheel
WHEEL=$(ls dist/*.whl)

FAILED_PROFILES=()

for profile in "${PROFILES[@]}"; do
    extras=$(extras_for_profile "$profile")
    venv_dir=".venv-smoke-${profile}"

    echo
    echo "=================================================================="
    echo " Profile: $profile  (extras='$extras')"
    echo "=================================================================="

    echo "--- Creating clean venv at $venv_dir ---"
    rm -rf "$venv_dir"
    if [ -n "$PYTHON_VERSION" ]; then
        uv venv "$venv_dir" --python "$PYTHON_VERSION"
    else
        uv venv "$venv_dir"
    fi

    echo "--- Installing built wheel with '$profile' dependencies only ---"
    VIRTUAL_ENV="$venv_dir" uv pip install "${WHEEL}${extras}"

    echo "--- Installed packages ---"
    VIRTUAL_ENV="$venv_dir" uv pip list

    echo "--- Running import smoke test ---"
    if ! "$venv_dir/bin/python" scripts/test_install_smoke.py "$profile"; then
        FAILED_PROFILES+=("$profile")
    fi
done

echo
echo "=================================================================="
if [ ${#FAILED_PROFILES[@]} -eq 0 ]; then
    echo " All profiles passed: ${PROFILES[*]}"
    exit 0
fi

echo " Failed profiles: ${FAILED_PROFILES[*]}" >&2
exit 1
