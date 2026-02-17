## Running the tests

1. Run all tests (excluding those requiring real DBs, see item 3):
    ```bash
    uv run pytest
    ```

    ```

    **Useful Flags:**
    - `-v` (verbose): Shows more detailed output, including each test name as it runs.
    - `-s` (no capture): Allows stdout (print statements) to show in the console. Useful for debugging.

    Example with flags:
    ```bash
    uv run pytest -v -s
    ```

    Note: Some tests require external databases (PostgreSQL, MySQL) and will be skipped if the corresponding environment variables (`POSTGRES_TEST_DSN`, `MYSQL_TEST_DSN`) are not set.

2. Run specific tests:
    ```bash
    # Run a specific test file
    uv run pytest tests/client/test_client_factory.py

    # Run a specific test function
    uv run pytest tests/client/test_client_factory.py::test_client_factory_connect_with_url

    # Run tests in a specific folder
    uv run pytest tests/client/
    ```

3. Run database integration tests (requires Docker):
    ```bash
    ./scripts/run_db_tests.sh
    ```

    This script will:
    - Start PostgreSQL and MySQL containers using Docker Compose.
    - Run the database integration tests.
    - Stop the containers after tests finish.

    You can also run tests for a specific database:
    ```bash
    ./scripts/run_db_tests.sh --postgres
    # or
    ./scripts/run_db_tests.sh --mysql
    ```

    To keep the databases running for debugging:
    ```bash
    ./scripts/run_db_tests.sh --debug
    ```
    (Follow the onscreen instructions to export DSNs and run pytest manually).

In case of failures, you can clean  up the cache:

1. `uv clean`
2. `rm -fR .pytest_cache .venv __pycache__`
