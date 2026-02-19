#!/bin/bash
set -e

# Get the directory of this script
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Docker compose file path
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.test.yml"

# Initialize variables
DEBUG_MODE=false
STOP_MODE=false
SERVICES=()
PYTEST_ARGS=()

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --debug)
      DEBUG_MODE=true
      shift
      ;;
    --stop)
      STOP_MODE=true
      shift
      ;;
    --postgres)
      SERVICES+=("postgres")
      shift
      ;;
    --mysql)
      SERVICES+=("mysql")
      shift
      ;;
    *)
      # Preserve other arguments for pytest
      PYTEST_ARGS+=("$1")
      shift
      ;;
  esac
done

# Handle --stop
if [[ "$STOP_MODE" == "true" ]]; then
  echo "Stopping test databases..."
  docker compose -f "$COMPOSE_FILE" down
  exit 0
fi

# Default to running both databases if none specified
if [[ ${#SERVICES[@]} -eq 0 ]]; then
  SERVICES=("postgres" "mysql")
fi

# Cleanup function to stop docker containers
cleanup() {
    echo "Stopping test databases..."
    docker compose -f "$COMPOSE_FILE" down
}

# Start the databases
echo "Starting/Verifying databases: ${SERVICES[*]}..."
docker compose -f "$COMPOSE_FILE" up -d --wait "${SERVICES[@]}"

# Set up environment variables based on active services
# Only export DSNs for started services so tests skip missing ones
for service in "${SERVICES[@]}"; do
  if [[ "$service" == "postgres" ]]; then
    export POSTGRES_TEST_DSN="postgresql+asyncpg://a2a:a2a_password@localhost:5432/a2a_test"
  elif [[ "$service" == "mysql" ]]; then
    export MYSQL_TEST_DSN="mysql+aiomysql://a2a:a2a_password@localhost:3306/a2a_test"
  fi
done

# Handle --debug mode
if [[ "$DEBUG_MODE" == "true" ]]; then
  echo "---------------------------------------------------"
  echo "Debug mode enabled. Databases are running."
  echo "You can connect to them using the following DSNs."
  echo ""
  echo "Run the following commands to set up your environment:"
  echo ""
  [[ -n "$POSTGRES_TEST_DSN" ]] && echo "export POSTGRES_TEST_DSN=\"$POSTGRES_TEST_DSN\""
  [[ -n "$MYSQL_TEST_DSN" ]] && echo "export MYSQL_TEST_DSN=\"$MYSQL_TEST_DSN\""
  echo ""
  echo "---------------------------------------------------"
  echo "Run ./scripts/run_integration_tests.sh --stop to shut databases down."
  exit 0
fi

# Register cleanup trap for normal test run
trap cleanup EXIT

# Run the tests
echo "Running integration tests..."
cd "$PROJECT_ROOT"

uv run pytest -v \
    tests/server/tasks/test_database_task_store.py \
    tests/server/tasks/test_database_push_notification_config_store.py \
    "${PYTEST_ARGS[@]}"
