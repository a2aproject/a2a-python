# Task: Check if anything needs to be fixed in ITK/TCK

## Todo
- [x] Clarify if "itk" means TCK or something else. (It is a directory `itk` with a sample agent)
- [x] Search for `itk` or `tck` in the codebase to identify relevant files. (Found `itk/main.py`)
- [x] Check if tests in those files need similar fixes (async card modifiers). (No fixes needed, doesn't use `card_modifier` or `helpers`)
- [ ] Run mandatory checks.

## Mandatory Checks
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
   ```bash
   uv run pytest --cov=src --cov-report=term-missing
   ```
