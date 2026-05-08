# AGENTS.md

Python SDK for the [Agent2Agent (A2A) Protocol](https://a2a-protocol.org/latest/specification/)
(`a2a` module, `a2a-sdk` distribution).  It handles complex messaging, task management, 
and communication across different transports (REST, gRPC, JSON-RPC).

## Technology Stack & Architecture

- **Language**: Python 3.10+
- **Package Manager**: `uv`
- **Lead Transports**: Starlette (REST/JSON-RPC), gRPC
- **Data Layer**: SQLAlchemy (SQL), Pydantic (Logic/Legacy), Protobuf (Modern Messaging)
- **Key Directories**:
    - `/src`: Core implementation logic.
    - `/tests`: Comprehensive test suite.
    - `/docs`: AI guides and migration documentation.

## Mandatory workflow

You MUST do all of the following:

1. **At the start of every task that touches files**, read
   `docs/ai/coding_conventions.md`, `docs/ai/mandatory_checks.md`,
   and `docs/ai/evidence_rules.md`.
2. **Before declaring any task done**, run the full check sequence
   in `docs/ai/mandatory_checks.md` — including for
   markdown/comment/whitespace-only changes.
3. **On any mistake**, load the `mistake-reflection` skill at
   `.agents/skills/mistake-reflection/SKILL.md` **before** continuing
   your response. The skill appends a structured entry to
   `docs/ai/ai_learnings.md` (gitignored local journal) so the user
   can use those findings to improve the workflow.

   When unsure: load the skill. False positives are free; false
   negatives are how the same mistake recurs.

## Layout footguns

- `src/a2a/types/` and `src/a2a/compat/v0_3/*_pb2*` — generated
  protobuf, **do not hand-edit**. Excluded from `ty`, `ruff`, coverage.
  Regenerate via `scripts/gen_proto.sh` /
  `scripts/gen_proto_compat.sh`.
- `tck/`, `itk/`, `tests/` — subprojects with their own
  `pyproject.toml`; not part of the main test run.
- `samples/` is minimal; real samples live in `a2aproject/a2a-samples`.

## Optional extras

`pyproject.toml` defines extras (`grpc`, `telemetry`, `postgresql`,
etc.). The dev group installs `a2a-sdk[all]`, so anything gated behind
an extra must still **import lazily** at runtime — the install-smoke
harness verifies this per profile.

