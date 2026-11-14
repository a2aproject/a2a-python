"""End-to-end smoke test for `samples/agent_card_signing.py`.

Runs the sample's demo mode as a subprocess, which serves a signed Agent Card
and then verifies it, asserting that a genuine card is accepted and that
tampered, unsigned and untrusted-key cards are all rejected.
"""

from __future__ import annotations

import asyncio
import socket
import sys

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_SCRIPT = REPO_ROOT / 'samples' / 'agent_card_signing.py'

DEMO_TIMEOUT_S = 60.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def _run_demo() -> str:
    """Run the sample in demo mode on a free port and return its output."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(SAMPLE_SCRIPT),
        'demo',
        '--port',
        str(_free_port()),
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=DEMO_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    output = stdout.decode('utf-8', errors='replace')
    assert proc.returncode == 0, (
        f'Sample exited with {proc.returncode}.\nOutput:\n{output}'
    )
    return output


@pytest.mark.asyncio
async def test_agent_card_signing_demo() -> None:
    """The sample should verify its own card and reject the forged ones."""
    output = await _run_demo()

    assert 'verified card for agent: Signed Card Agent' in output, output
    # One rejection each for the tampered, unsigned and untrusted-key cards.
    assert output.count('rejected as expected') == 3, output
    assert 'ERROR:' not in output, output
