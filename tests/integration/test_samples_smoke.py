"""End-to-end smoke test for `samples/hello_world_agent.py` and `samples/cli.py`.

Boots the sample agent as a subprocess on free ports, then runs the sample CLI
against it once per supported transport, asserting the expected greeting reply
flows through.
"""

from __future__ import annotations

import asyncio
import socket
import sys

from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / 'samples'
AGENT_SCRIPT = SAMPLES_DIR / 'hello_world_agent.py'
CLI_SCRIPT = SAMPLES_DIR / 'cli.py'

STARTUP_TIMEOUT_S = 30.0
CLI_TIMEOUT_S = 30.0
EXPECTED_REPLY = 'Hello World! Nice to meet you!'


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def _wait_for_agent_card(url: str) -> None:
    deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT_S
    async with httpx.AsyncClient(timeout=2.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    return
            except httpx.RequestError:
                pass
            await asyncio.sleep(0.2)
    raise TimeoutError(f'Agent did not become ready at {url}')


@pytest_asyncio.fixture
async def running_sample_agent() -> AsyncGenerator[str, None]:
    """Start `hello_world_agent.py` as a subprocess on free ports."""
    host = '127.0.0.1'
    http_port = _free_port()
    grpc_port = _free_port()
    compat_grpc_port = _free_port()
    base_url = f'http://{host}:{http_port}'

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(AGENT_SCRIPT),
        '--host',
        host,
        '--port',
        str(http_port),
        '--grpc-port',
        str(grpc_port),
        '--compat-grpc-port',
        str(compat_grpc_port),
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    try:
        await _wait_for_agent_card(f'{base_url}/.well-known/agent-card.json')
        yield base_url
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


async def _run_cli(base_url: str, transport: str) -> str:
    """Run `cli.py --transport <transport>`, send `hello`, return combined output."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(CLI_SCRIPT),
        '--url',
        base_url,
        '--transport',
        transport,
        cwd=str(REPO_ROOT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(b'hello\n/quit\n'),
            timeout=CLI_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    output = stdout.decode('utf-8', errors='replace')
    assert proc.returncode == 0, (
        f'CLI exited with {proc.returncode} for transport {transport!r}.\n'
        f'Output:\n{output}'
    )
    return output


@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['JSONRPC', 'HTTP+JSON', 'GRPC'])
async def test_cli_against_sample_agent(
    running_sample_agent: str, transport: str
) -> None:
    """The CLI should successfully exchange a greeting over each transport."""
    output = await _run_cli(running_sample_agent, transport)

    assert 'TASK_STATE_COMPLETED' in output, output
    assert EXPECTED_REPLY in output, output
