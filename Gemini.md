**A2A specification:** https://a2a-protocol.org/latest/specification/

## Project frameworks
- uv as package manager

## How to run all tests
1. If dependencies are not installed, install them using the following command
   ```
   uv sync --all-extras 
   ```

2. Run tests
  ```
  uv run pytest
  ```

## Other instructions
1. Whenever writing python code, write types as well.
2. After making the changes run ruff to check and fix the formatting issues
   ```
   uv run ruff check --fix
   ```
3. Run mypy type checkers to check for type errors
   ```
   uv run mypy
   ```
4. Run the unit tests to make sure that none of the unit tests are broken.
