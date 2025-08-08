# A2A Python SDK

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/a2a-sdk)](https://pypi.org/project/a2a-sdk/)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/a2a-sdk)
[![PyPI - Downloads](https://img.shields.io/pypi/dw/a2a-sdk)](https://pypistats.org/packages/a2a-sdk)

<!-- markdownlint-disable no-inline-html -->

<html>
   <h2 align="center">
   <img src="https://raw.githubusercontent.com/a2aproject/A2A/refs/heads/main/docs/assets/a2a-logo-black.svg" width="256" alt="A2A Logo"/>
   </h2>
   <h3 align="center">A Python library that helps run agentic applications as A2AServers following the <a href="https://a2a-protocol.org">Agent2Agent (A2A) Protocol</a>.</h3>
</html>

<!-- markdownlint-enable no-inline-html -->

## Installation

You can install the A2A SDK using either `uv` or `pip`.

## Prerequisites

- Python 3.10+
- `uv` (optional, but recommended) or `pip`

### Using `uv`

When you're working within a uv project or a virtual environment managed by uv, the preferred way to add packages is using uv add.

```bash
uv add a2a-sdk
```

To include the optional HTTP server components (FastAPI, Starlette), install the `http-server` extra:

```bash
uv add a2a-sdk[http-server]
```

To install with gRPC support:

```bash
uv add "a2a-sdk[grpc]"
```

To install with OpenTelemetry tracing support:

```bash
uv add "a2a-sdk[telemetry]"
```

To install with database support:

```bash
# PostgreSQL support
uv add "a2a-sdk[postgresql]"

# MySQL support
uv add "a2a-sdk[mysql]"

# SQLite support
uv add "a2a-sdk[sqlite]"

# All database drivers
uv add "a2a-sdk[sql]"
```

### Using `pip`

If you prefer to use pip, the standard Python package installer, you can install `a2a-sdk` as follows

```bash
pip install a2a-sdk
```

To include the optional HTTP server components (FastAPI, Starlette), install the `http-server` extra:

```bash
pip install a2a-sdk[http-server]
```

To install with gRPC support:

```bash
pip install "a2a-sdk[grpc]"
```

To install with OpenTelemetry tracing support:

```bash
pip install "a2a-sdk[telemetry]"
```

To install with database support:

```bash
# PostgreSQL support
pip install "a2a-sdk[postgresql]"

# MySQL support
pip install "a2a-sdk[mysql]"

# SQLite support
pip install "a2a-sdk[sqlite]"

# All database drivers
pip install "a2a-sdk[sql]"
```

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

You can also find more Python samples [here](https://github.com/a2aproject/a2a-samples/tree/main/samples/python) and JavaScript samples [here](https://github.com/a2aproject/a2a-samples/tree/main/samples/js).

## License

This project is licensed under the terms of the [Apache 2.0 License](https://raw.githubusercontent.com/a2aproject/a2a-python/refs/heads/main/LICENSE).

## Contributing

See [CONTRIBUTING.md](https://github.com/a2aproject/a2a-python/blob/main/CONTRIBUTING.md) for contribution guidelines.
