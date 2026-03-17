# Agent Command Center

## 1. Project Overview & Purpose
**Primary Goal**: This is the Python SDK for the Agent2Agent (A2A) Protocol. It allows developers to build and run agentic applications as A2A-compliant servers. It handles complex messaging, task management, and communication across different transports (REST, gRPC, JSON-RPC).
**Specification**: [A2A-Protocol](https://a2a-protocol.org/latest/specification/)

## 2. Technology Stack & Architecture

- **Language**: Python 3.10+
- **Package Manager**: `uv`
- **Lead Transports**: FastAPI (REST/JSON-RPC), gRPC
- **Data Layer**: SQLAlchemy (SQL), Pydantic (Logic/Legacy), Protobuf (Modern Messaging)
- **Key Directories**:
    - `/src`: Core implementation logic.
    - `/tests`: Comprehensive test suite.
    - `/docs`: AI guides.

## 3. Style Guidelines & Mandatory Checks
- **Style Guidelines**: Follow the rules in @./docs/ai/coding_conventions.md for every response involving code.
- **Mandatory Checks**: Run the commands in @./docs/ai/mandatory_checks.md after making any changes to the code and before committing.


## 4. Mandatory AI Workflow for Coding Tasks
1. **Required Reading**: You MUST use `view_file` to read the contents of @./docs/ai/coding_conventions.md and @./docs/ai/mandatory_checks.md at the very beginning of EVERY coding task.
2. **Initial Checklist**: Every `task.md` you create MUST include a section for **Mandatory Checks** from @./docs/ai/mandatory_checks.md.
3. **Verification Requirement**: You MUST run all mandatory checks before declaring any task finished.
