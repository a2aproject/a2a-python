### Coding Conventions & Style Guide

Non-negotiable rules for code quality and style.

1. **Python Types**: All Python code MUST include type hints. All function definitions MUST include return types.
2. **Type Safety**: All code MUST pass `mypy` and `pyright` checks.
3. **Formatting & Linting**: All code MUST be formatted and linted with `ruff`.

#### Examples:

**Correct Typing:**
```python
async def get_task_status(task: Task) -> TaskStatus:
    return task.status
```

**Incorrect (Do NOT do this):**
```python
def get_task(task): # Missing type hints
    return task.status # Potential None return without handling
```
