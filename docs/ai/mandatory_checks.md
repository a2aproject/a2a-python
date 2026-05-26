# Mandatory Checks

Run in this order before declaring any task done — including for
markdown/comment/whitespace-only changes:

```bash
./scripts/lint.sh        # ruff check --fix, ruff format, ty check
uv run pytest

# Only before commit, when src/ changed:
uv run pytest --cov=src --cov-report=term-missing
```

CI enforces `--cov-fail-under=88` on the `a2a` package.
