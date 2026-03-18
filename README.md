# A2A Python SDK

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/a2a-sdk)](https://pypi.org/project/a2a-sdk/)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/a2a-sdk)
[![PyPI - Downloads](https://img.shields.io/pypi/dw/a2a-sdk)](https://pypistats.org/packages/a2a-sdk)
[![Python Unit Tests](https://github.com/a2aproject/a2a-python/actions/workflows/unit-tests.yml/badge.svg)](https://github.com/a2aproject/a2a-python/actions/workflows/unit-tests.yml)
<!-- markdownlint-disable no-inline-html -->
<a href="https://codewiki.google/github.com/a2aproject/a2a-python">
  <img src="https://www.gstatic.com/_/boq-sdlc-agents-ui/_/r/Mvosg4klCA4.svg" alt="Ask Code Wiki" height="20">
</a>

<div align="center">
   <img src="https://raw.githubusercontent.com/a2aproject/A2A/refs/heads/main/docs/assets/a2a-logo-black.svg" width="256" alt="A2A Logo"/>
   <h3>
      A Python library for running agentic applications as A2A Servers, following the <a href="https://a2a-protocol.org">Agent2Agent (A2A) Protocol</a>.
   </h3>
</div>

<!-- markdownlint-enable no-inline-html -->

---

## ✨ Features

- **A2A Protocol Compliant:** Build agentic applications that adhere to the Agent2Agent (A2A) Protocol.
- **Extensible:** Easily add support for different communication protocols and database backends.
- **Asynchronous:** Built on modern async Python for high performance.
- **Optional Integrations:** Includes optional support for:
  - HTTP servers ([FastAPI](https://fastapi.tiangolo.com/), [Starlette](https://www.starlette.io/))
  - [gRPC](https://grpc.io/)
  - [OpenTelemetry](https://opentelemetry.io/) for tracing
  - SQL databases ([PostgreSQL](https://www.postgresql.org/), [MySQL](https://www.mysql.com/), [SQLite](https://sqlite.org/))

---

## 🧩 Compatibility

This SDK implements the A2A Protocol Specification [`0.3`](https://a2a-protocol.org/v0.3.0/specification).

> [!IMPORTANT]
> There is an [**alpha version**](https://github.com/a2aproject/a2a-python/releases?q=%22v1.0.0-alpha%22&expanded=true) available with support for both [`1.0`](https://a2a-protocol.org/v1.0.0/specification/) and [`0.3`](https://a2a-protocol.org/v0.3.0/specification) versions. Development for this version is taking place in the [`1.0-dev`](https://github.com/a2aproject/a2a-python/tree/1.0-dev) branch, tracked in [#701](https://github.com/a2aproject/a2a-python/issues/701).

| Transport | Client | Server |
| :--- | :---: | :---: |
| **JSON-RPC** | ✅ | ✅ |
| **HTTP+JSON/REST** | ✅ | ✅ |
| **GRPC** | ✅ | ✅ |

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- `uv` (recommended) or `pip`

### 🔧 Installation

Install the core SDK and any desired extras using your preferred package manager.

| Feature                  | `uv` Command                               | `pip` Command                                |
| ------------------------ | ------------------------------------------ | -------------------------------------------- |
| **Core SDK**             | `uv add a2a-sdk`                           | `pip install a2a-sdk`                        |
| **All Extras**           | `uv add "a2a-sdk[all]"`                    | `pip install "a2a-sdk[all]"`                 |
| **HTTP Server**          | `uv add "a2a-sdk[http-server]"`            | `pip install "a2a-sdk[http-server]"`         |
| **gRPC Support**         | `uv add "a2a-sdk[grpc]"`                   | `pip install "a2a-sdk[grpc]"`                |
| **OpenTelemetry Tracing**| `uv add "a2a-sdk[telemetry]"`              | `pip install "a2a-sdk[telemetry]"`           |
| **Encryption**           | `uv add "a2a-sdk[encryption]"`             | `pip install "a2a-sdk[encryption]"`          |
| **Vertex AI Task Store** | `uv add "a2a-sdk[vertex]"`                 | `pip install "a2a-sdk[vertex]"`              |
|                          |                                            |                                              |
| **Database Drivers**     |                                            |                                              |
| **PostgreSQL**           | `uv add "a2a-sdk[postgresql]"`             | `pip install "a2a-sdk[postgresql]"`          |
| **MySQL**                | `uv add "a2a-sdk[mysql]"`                  | `pip install "a2a-sdk[mysql]"`               |
| **SQLite**               | `uv add "a2a-sdk[sqlite]"`                 | `pip install "a2a-sdk[sqlite]"`              |
| **All SQL Drivers**      | `uv add "a2a-sdk[sql]"`                    | `pip install "a2a-sdk[sql]"`                 |

## Examples

### [Helloworld Example](https://github.com/a2aproject/a2a-samples/tree/main/samples/python/agents/helloworld)

1. Run Remote Agent

   ```bash
   git clone https://github.com/a2aproject/a2a-samples.git
   cd a2a-samples/samples/python/agents/helloworld
   uv run .
   ```

2. In another terminal, run the client

   ```bash
   cd a2a-samples/samples/python/agents/helloworld
   uv run test_client.py
   ```

3. You can validate your agent using the agent inspector. Follow the instructions at the [a2a-inspector](https://github.com/a2aproject/a2a-inspector) repo.

---

## 🌐 More Examples

You can find a variety of more detailed examples in the [a2a-samples](https://github.com/a2aproject/a2a-samples) repository:

- **[Python Examples](https://github.com/a2aproject/a2a-samples/tree/main/samples/python)**
- **[JavaScript Examples](https://github.com/a2aproject/a2a-samples/tree/main/samples/js)**

---

## 🤝 Contributing

Contributions are welcome! Please see the [CONTRIBUTING.md](CONTRIBUTING.md) file for guidelines on how to get involved.

---

## 📄 License

This project is licensed under the Apache 2.0 License. See the [LICENSE](LICENSE) file for more details.
