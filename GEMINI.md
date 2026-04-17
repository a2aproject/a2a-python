# Agent Command Center

## 1. Project Overview & Purpose
**Primary Goal**: This is the Python SDK for the Agent2Agent (A2A) Protocol. It allows developers to build and run agentic applications as A2A-compliant servers. It handles complex messaging, task management, and communication across different transports (REST, gRPC, JSON-RPC).
**Specification**: [A2A-Protocol](https://a2a-protocol.org/latest/specification/)

## 2. Technology Stack & Architecture

- **Language**: Python 3.10+
- **Package Manager**: `uv`
- **Lead Transports**: Starlette (REST/JSON-RPC), gRPC
- **Data Layer**: SQLAlchemy (SQL), Pydantic (Logic/Legacy), Protobuf (Modern Messaging)
- **Key Directories**:
    - `/src`: Core implementation logic.
    - `/tests`: Comprehensive test suite.
    - `/docs`: AI guides.

## 3. Style Guidelines & Mandatory Checks
- **Style Guidelines**: Follow the rules in @./docs/ai/coding_conventions.md for every response involving code.
- **Mandatory Checks**: Run the commands in @./docs/ai/mandatory_checks.md after making any changes to the code and before committing.

## 4. Mandatory AI Workflow for Coding Tasks
1. **Required Reading**: You MUST read the contents of @./docs/ai/coding_conventions.md and @./docs/ai/mandatory_checks.md at the very beginning of EVERY coding task.
2. **Initial Checklist**: Every `task.md` you create MUST include a section for **Mandatory Checks** from @./docs/ai/mandatory_checks.md.
3. **Verification Requirement**: You MUST run all mandatory checks before declaring any task finished.

## 5. Mistake Reflection Protocol

> [!NOTE] for Users:
> `docs/ai/ai_learnings.md` is a local-only file (excluded from git) meant to be
> read by the developer to improve AI assistant behavior on this project. Use its
> findings to improve the GEMINI.md setup.

When you realise you have made a mistake — whether caught by the user,
by a tool, or by your own reasoning — you MUST:

1. **Acknowledge the mistake explicitly** and explain what went wrong.
2. **Reflect on the root cause**: was it a missing check, a false assumption, skipped verification, or a gap in the workflow?
3. **Immediately append a new entry to `docs/ai/ai_learnings.md`** — this is not optional and does not require user confirmation. Do it before continuing, then update the user about the workflow change.

   **Entry format:**
   - **Mistake**: What went wrong.
   - **Root cause**: Why it happened.
   - **Rule**: The concrete rule added to prevent recurrence.

The goal is to treat every mistake as a signal that the workflow is
incomplete, and to improve it in place so the same mistake cannot
happen again.
