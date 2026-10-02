"""Launches N cluster-mode replicas as subprocesses on consecutive ports.

Usage:
    python -m samples.clustermode.run_cluster --replicas 3 --base-port 41241

Each replica shares one database (SQLite file by default). Point any HTTP client
at any replica's /a2a/jsonrpc endpoint, or use exercise.py which deliberately
spreads requests for one task across replicas.
"""

import argparse
import contextlib
import os
import signal
import subprocess
import sys
import time

from pathlib import Path

from samples.clustermode.cluster_common import default_sqlite_path


def main() -> None:
    """Launches N replica subprocesses on consecutive ports."""
    parser = argparse.ArgumentParser(description='Run an A2A replica cluster')
    parser.add_argument('--replicas', type=int, default=3)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--base-port', type=int, default=41241)
    parser.add_argument('--ticks', type=int, default=10)
    args = parser.parse_args()

    # A fresh shared SQLite file for this run (unless a DSN is configured).
    if 'A2A_CLUSTER_DSN' not in os.environ:
        db_path = os.environ.setdefault(
            'A2A_CLUSTER_SQLITE', default_sqlite_path()
        )
        with contextlib.suppress(FileNotFoundError):
            Path(db_path).unlink()

    procs: list[subprocess.Popen] = []
    ports = [args.base_port + i for i in range(args.replicas)]
    try:
        for i, port in enumerate(ports):
            cmd = [
                sys.executable,
                '-m',
                'samples.clustermode.server',
                '--host',
                args.host,
                '--port',
                str(port),
                '--replica-id',
                f'replica-{i}',
                '--ticks',
                str(args.ticks),
            ]
            procs.append(
                subprocess.Popen(cmd, env=os.environ.copy())  # noqa: S603
            )
            time.sleep(0.5)

        print('\nCluster running. Replica endpoints:')
        for i, port in enumerate(ports):
            print(f'  replica-{i}: http://{args.host}:{port}/a2a/jsonrpc')
        print('\nExercise it with:')
        joined = ','.join(str(p) for p in ports)
        print(
            f'  python -m samples.clustermode.exercise '
            f'--host {args.host} --ports {joined}'
        )
        print('\nPress Ctrl+C to stop.\n')

        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            with contextlib.suppress(ProcessLookupError):
                p.send_signal(signal.SIGINT)
        for p in procs:
            with contextlib.suppress(Exception):
                p.wait(timeout=5)


if __name__ == '__main__':
    main()
