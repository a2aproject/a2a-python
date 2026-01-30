## Running the tests

1. Run the tests
    ```bash
    uv run pytest -v -s client/test_client_factory.py
    ```

In case of failures, you can clean up the cache:

1. `uv clean`
2. `rm -fR .pytest_cache .venv __pycache__`
