import contextlib
import socket
import time

from multiprocessing import Process

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


def create_app_process(app, host, port) -> Process:
    """Creates a separate process for a given application."""
    return Process(
        target=run_server,
        args=(app, host, port),
        daemon=True,
    )
