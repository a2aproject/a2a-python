### Test and Fix Commands

Exact shell commands required to test the project and fix formatting issues.

1. **Formatting & Linting**:
   ```bash
   uv run ruff check --fix
   uv run ruff format
   ```

2. **Type Checking**:
   ```bash
   uv run mypy src
   uv run pyright src
   ```

3. **Testing**:
   ```bash
   uv run pytest
   ```

4. **Coverage**:
Only run this command after adding new source code and before committing.
   ```bash
   uv run pytest --cov=src --cov-report=term-missing
   ```
