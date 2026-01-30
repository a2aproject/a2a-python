#!/usr/bin/env python3
import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

# Constants
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
COMPOSE_FILE = SCRIPT_DIR / "docker-compose.test.yml"

DSNS = {
    "postgres": "postgresql+asyncpg://a2a:a2a_password@localhost:5432/a2a_test",
    "mysql": "mysql+aiomysql://a2a:a2a_password@localhost:3306/a2a_test",
}

def run_command(command, cwd=None, env=None, check=True):
    """Runs a shell command."""
    try:
        subprocess.run(command, cwd=cwd, env=env, check=check)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)

def stop_databases():
    """Stops the test databases."""
    print("Stopping test databases...")
    run_command(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down"],
        check=False
    )

def main():
    parser = argparse.ArgumentParser(
        description="Run integration tests with Docker databases.",
        add_help=False  # handle help manually to pass args to pytest
    )
    parser.add_argument("--debug", action="store_true", help="Start DBs and exit without running tests")
    parser.add_argument("--stop", action="store_true", help="Stop the databases and exit")
    parser.add_argument("--postgres", action="store_true", help="Use PostgreSQL")
    parser.add_argument("--mysql", action="store_true", help="Use MySQL")
    parser.add_argument("--help", action="store_true", help="Show this help message")

    # Parse known args, leave the rest for pytest
    args, pytest_args = parser.parse_known_args()

    if args.help:
        parser.print_help()
        print("\nAny other arguments will be passed to pytest.")
        sys.exit(0)

    if args.stop:
        stop_databases()
        sys.exit(0)

    # Determine which services to run
    services = []
    if args.postgres:
        services.append("postgres")
    if args.mysql:
        services.append("mysql")
    
    # Default to both if neither is specified
    if not services:
        services = ["postgres", "mysql"]

    print(f"Starting/Verifying databases: {', '.join(services)}...")
    run_command(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait"] + services
    )

    # Prepare environment variables
    env = os.environ.copy()
    for service in services:
        if service == "postgres":
            env["POSTGRES_TEST_DSN"] = DSNS["postgres"]
        elif service == "mysql":
            env["MYSQL_TEST_DSN"] = DSNS["mysql"]

    if args.debug:
        print("-" * 51)
        print("Debug mode enabled. Databases are running.")
        print("You can connect to them using the following DSNs:")
        if "postgres" in services:
            print(f"Postgres: {DSNS['postgres']}")
        if "mysql" in services:
            print(f"MySQL:    {DSNS['mysql']}")
        print("-" * 51)
        print(f"Run {sys.argv[0]} --stop to shut them down.")
        sys.exit(0)

    # Register cleanup on normal exit or signals
    def signal_handler(sig, frame):
        stop_databases()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        print("Running integration tests...")
        
        test_files = [
            "tests/server/tasks/test_database_task_store.py",
            "tests/server/tasks/test_database_push_notification_config_store.py",
        ]
        
        cmd = ["uv", "run", "--extra", "all", "pytest", "-v"] + test_files + pytest_args
        run_command(cmd, cwd=PROJECT_ROOT, env=env)
        
    finally:
        # Always cleanup unless in debug mode (which exits earlier)
        stop_databases()

if __name__ == "__main__":
    main()
