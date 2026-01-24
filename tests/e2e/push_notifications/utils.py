import contextlib
import multiprocessing
import socket
import sys
import time

import httpx
import uvicorn


def find_free_port():
    """Finds and returns an available ephemeral localhost port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def run_server(app, host, port) -> None:
    """Runs a uvicorn server."""
    uvicorn.run(app, host=host, port=port, log_level='warning')


def wait_for_server_ready(url: str, timeout: int = 10) -> None:
    """Polls the provided URL endpoint until the server is up."""
    start_time = time.time()
    while True:
        with contextlib.suppress(httpx.ConnectError):
            with httpx.Client() as client:
                response = client.get(url)
                if response.status_code == 200:
                    return
        if time.time() - start_time > timeout:
            raise TimeoutError(
                f'Server at {url} failed to start after {timeout}s'
            )
        time.sleep(0.1)


def create_app_process(app, host, port) -> multiprocessing.Process:
    """Creates a separate process for a given application.

    Uses 'fork' context on non-Windows platforms to avoid pickle issues
    with FastAPI apps (which have closures that can't be pickled).
    """
    # Use fork on Unix-like systems to avoid pickle issues with FastAPI
    if sys.platform != 'win32':
        ctx = multiprocessing.get_context('fork')
    else:
        ctx = multiprocessing.get_context('spawn')

    return ctx.Process(
        target=run_server,
        args=(app, host, port),
        daemon=True,
    )
