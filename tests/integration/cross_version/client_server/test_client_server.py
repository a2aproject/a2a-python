import os
import shutil
import socket
import subprocess
import time

import pytest
import select
import signal


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait_for_port(proc: subprocess.Popen, proc_name: str, port, timeout=5.0):
    start_time = time.time()
    while time.time() - start_time < timeout:
        print(
            f'Waiting for port {port} to be available for {timeout - (time.time() - start_time)} seconds...'
        )
        try:
            if proc.poll() is not None:
                print(
                    f'Process {proc_name} died before port {port} was available'
                )
                return False
            with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def get_env(script: str) -> dict[str, str]:
    new_env = os.environ.copy()
    new_env['PYTHONUNBUFFERED'] = '1'
    if '_1_0.py' in script:
        new_env['PYTHONPATH'] = (
            os.path.abspath('src') + ':' + new_env.get('PYTHONPATH', '')
        )
    return new_env


def finalize_process(
    proc: subprocess.Popen,
    name: str,
    expected_return_code=None,
    timeout: float = 5.0,
):
    failure = False
    if expected_return_code is not None:
        try:
            print(f'Waiting for process {name} to finish...')
            if proc.wait(timeout=timeout) != expected_return_code:
                print(
                    f'Process {name} returned code {proc.returncode}, expected {expected_return_code}'
                )
                failure = True
        except subprocess.TimeoutExpired:
            print(f'Process {name} timed out after {timeout} seconds')
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            failure = True
    else:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            print(f'Process {name} already terminated!')
            failure = True

    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

    print(f'Process {name} finished with code {proc.wait()}')

    stdout_text, stderr_text = proc.communicate(timeout=3.0)

    print('-' * 80)
    print(f'Process {name} STDOUT:\n{stdout_text}')
    print('-' * 80)
    print(f'Process {name} STDERR:\n{stderr_text}')
    print('-' * 80)
    if failure:
        pytest.fail(f'Process {name} failed.')


@pytest.fixture(scope='session')
def running_servers():
    uv_path = shutil.which('uv')
    if not os.path.exists(uv_path):
        pytest.fail(f"Could not find 'uv' executable at {uv_path}")

    # Server 1.0 setup
    s10_http_port = get_free_port()
    s10_grpc_port = get_free_port()
    s10_deps = ['--with', 'uvicorn', '--with', 'fastapi', '--with', 'grpcio']
    s10_cmd = (
        [uv_path, 'run']
        + s10_deps
        + [
            'python',
            'tests/integration/cross_version/client_server/server_1_0.py',
            '--http-port',
            str(s10_http_port),
            '--grpc-port',
            str(s10_grpc_port),
        ]
    )
    s10_proc = subprocess.Popen(
        s10_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=get_env('server_1_0.py'),
        text=True,
        start_new_session=True,
    )

    # Server 0.3 setup
    s03_http_port = get_free_port()
    s03_grpc_port = get_free_port()
    s03_deps = [
        '--with',
        'a2a-sdk[grpc]==0.3.24',
        '--with',
        'uvicorn',
        '--with',
        'fastapi',
        '--no-project',
    ]
    s03_cmd = (
        [uv_path, 'run']
        + s03_deps
        + [
            'python',
            'tests/integration/cross_version/client_server/server_0_3.py',
            '--http-port',
            str(s03_http_port),
            '--grpc-port',
            str(s03_grpc_port),
        ]
    )
    s03_proc = subprocess.Popen(
        s03_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=get_env('server_0_3.py'),
        text=True,
        start_new_session=True,
    )

    try:
        # Wait for ports
        assert wait_for_port(
            s10_proc, 'server_1_0.py', s10_http_port, timeout=3.0
        ), 'Server 1.0 HTTP failed to start'
        assert wait_for_port(
            s10_proc, 'server_1_0.py', s10_grpc_port, timeout=3.0
        ), 'Server 1.0 GRPC failed to start'
        assert wait_for_port(
            s03_proc, 'server_0_3.py', s03_http_port, timeout=3.0
        ), 'Server 0.3 HTTP failed to start'
        assert wait_for_port(
            s03_proc, 'server_0_3.py', s03_grpc_port, timeout=3.0
        ), 'Server 0.3 GRPC failed to start'

        print('SERVER READY')

        yield {
            'server_1_0.py': s10_http_port,
            'server_0_3.py': s03_http_port,
            'uv_path': uv_path,
            'procs': {'server_1_0.py': s10_proc, 'server_0_3.py': s03_proc},
        }

    finally:
        print('SERVER CLEANUP')
        for proc, name in [
            (s03_proc, 'server_0_3.py'),
            (s10_proc, 'server_1_0.py'),
        ]:
            finalize_process(proc, name)


@pytest.mark.timeout(15)
@pytest.mark.parametrize(
    'server_script, client_script, client_deps, protocols',
    [
        # Run 0.3 Server <-> 0.3 Client
        (
            'server_0_3.py',
            'client_0_3.py',
            ['--with', 'a2a-sdk[grpc]==0.3.24', '--no-project'],
            ['grpc', 'jsonrpc', 'rest'],
        ),
        # Run 1.0 Server <-> 0.3 Client
        (
            'server_1_0.py',
            'client_0_3.py',
            ['--with', 'a2a-sdk[grpc]==0.3.24', '--no-project'],
            ['grpc', 'jsonrpc', 'rest'],
        ),
        # Run 1.0 Server <-> 1.0 Client
        (
            'server_1_0.py',
            'client_1_0.py',
            [],
            ['grpc', 'jsonrpc', 'rest'],
        ),
        # Run 0.3 Server <-> 1.0 Client
        (
            'server_0_3.py',
            'client_1_0.py',
            [],
            ['grpc', 'jsonrpc', 'rest'],
        ),
    ],
)
def test_cross_version(
    running_servers, server_script, client_script, client_deps, protocols
):
    http_port = running_servers[server_script]
    uv_path = running_servers['uv_path']

    card_url = f'http://127.0.0.1:{http_port}/jsonrpc/'
    client_cmd = (
        [uv_path, 'run']
        + client_deps
        + [
            'python',
            f'tests/integration/cross_version/client_server/{client_script}',
            '--url',
            card_url,
            '--protocols',
        ]
        + protocols
    )

    client_result = subprocess.Popen(
        client_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=get_env(client_script),
        text=True,
        start_new_session=True,
    )
    finalize_process(client_result, client_script, 0)
