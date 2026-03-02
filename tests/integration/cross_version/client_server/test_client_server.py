import subprocess
import time
import socket
import pytest
import shutil
import os

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def wait_for_port(port, timeout=5.0):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.1)
    return False

def get_env(script):
    new_env = os.environ.copy()
    if "_1_0.py" in script:
        new_env["PYTHONPATH"] = os.path.abspath("src") + ":" + new_env.get("PYTHONPATH", "")
    return new_env

@pytest.fixture(scope="session")
def running_servers():
    uv_path = shutil.which("uv")
    if not os.path.exists(uv_path):
        pytest.fail(f"Could not find 'uv' executable at {uv_path}")

    # Server 1.0 setup
    s1_http_port = get_free_port()
    s1_grpc_port = get_free_port()
    s1_deps = ["--with", "uvicorn", "--with", "fastapi", "--with", "grpcio"]
    s1_cmd = [
        uv_path, "run"
    ] + s1_deps + [
        "python", "tests/integration/cross_version/client_server/server_1_0.py",
        "--http-port", str(s1_http_port),
        "--grpc-port", str(s1_grpc_port)
    ]
    s1_proc = subprocess.Popen(s1_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=get_env("server_1_0.py"))

    # Server 0.3 setup
    s03_http_port = get_free_port()
    s03_grpc_port = get_free_port()
    s03_deps = ["--with", "a2a-sdk[grpc]==0.3.24", "--with", "uvicorn", "--with", "fastapi", "--no-project"]
    s03_cmd = [
        uv_path, "run"
    ] + s03_deps + [
        "python", "tests/integration/cross_version/client_server/server_0_3.py",
        "--http-port", str(s03_http_port),
        "--grpc-port", str(s03_grpc_port)
    ]
    s03_proc = subprocess.Popen(s03_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=get_env("server_0_3.py"))

    # Wait for ports
    assert wait_for_port(s1_http_port, timeout=15.0), "Server 1.0 HTTP failed to start"
    assert wait_for_port(s1_grpc_port, timeout=15.0), "Server 1.0 GRPC failed to start"
    assert wait_for_port(s03_http_port, timeout=15.0), "Server 0.3 HTTP failed to start"
    assert wait_for_port(s03_grpc_port, timeout=15.0), "Server 0.3 GRPC failed to start"

    yield {
        "server_1_0.py": s1_http_port,
        "server_0_3.py": s03_http_port,
        "uv_path": uv_path,
        "procs": {"server_1_0.py": s1_proc, "server_0_3.py": s03_proc}
    }

    # Cleanup
    for proc in [s1_proc, s03_proc]:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    "server_script, client_script, client_deps",
    [
        # Run 1.0 <-> 1.0
        ("server_1_0.py", "client_1_0.py", ["--with", "grpcio"]),
        # Run 0.3 <-> 0.3
        ("server_0_3.py", "client_0_3.py", ["--with", "a2a-sdk[grpc]==0.3.24", "--no-project"]),
        # Run 1.0 Server <-> 0.3 Client
        ("server_1_0.py", "client_0_3.py", ["--with", "a2a-sdk[grpc]==0.3.24", "--no-project"]),
        # Run 0.3 Server <-> 1.0 Client
        ("server_0_3.py", "client_1_0.py", ["--with", "grpcio"])
    ]
)
def test_cross_version(running_servers, server_script, client_script, client_deps):
    http_port = running_servers[server_script]
    uv_path = running_servers["uv_path"]

    card_url = f"http://127.0.0.1:{http_port}/jsonrpc/"
    client_cmd = [
        uv_path, "run"
    ] + client_deps + [
        "python", f"tests/integration/cross_version/client_server/{client_script}",
        "--url", card_url,
        "--protocols", "jsonrpc", "rest", "grpc"
    ]
    
    client_result = subprocess.run(client_cmd, capture_output=True, text=True, env=get_env(client_script))
    
    if client_result.returncode != 0 or "Success:" not in client_result.stdout:
        # Pull stdout/stderr from the background process non-blockingly (if they failed)
        # Note: Since the servers are shared, we just print whatever has been collected if it failed,
        # but they are still running for other tests.
        pytest.fail(f"Client failed:\nSTDOUT:\n{client_result.stdout}\nSTDERR:\n{client_result.stderr}")
