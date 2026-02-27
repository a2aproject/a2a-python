**A2A specification:** https://a2a-protocol.org/latest/specification/

## Project frameworks
- uv as package manager

## Code style and mandatory checks
1. Whenever writing python code, write types as well.
2. After making the changes run ruff to check and fix the formatting issues
   ```
   uv run ruff check --fix
   uv run ruff format
   ```
3. Run mypy type checkers to check for type errors
   ```
   uv run mypy src
   ```
4. Run the unit tests to make sure that none of the unit tests are broken.
  ```
  uv run pytest
  ```
