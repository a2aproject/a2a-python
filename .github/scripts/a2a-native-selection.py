#!/usr/bin/env python3
"""Build the consumer-selected manifest for a retained A2A native packet.

The reader refuses a selection whose ``reader_sources`` / ``installed_sources`` maps
differ from the checkout it is pointed at, and whose ``native_pins`` do not describe
the packet it is given — so the manifest has to be generated, not hand-written.

Disclosure that belongs with the output: this selection is derived from the
producer's own retained declarations (``selected-native-pins.json``, ``report.json``)
plus the seven retained source files. Per
``packages/agent-evidence-observer/docs/NATIVE-CONSUMER-CI.md`` that makes it a
**reproduction-grade** selection: it shows the reader and the retained bytes agree,
not that an independent party accepted these pins.

Usage:
  a2a-native-selection.py --packet DIR --checkout DIR --reader-revision SHA40 \
      --selection-id ID --out FILE
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

FORMAT = "probity-native-consumer-selection-v1"
PROFILE = "a2a"
# The seven retained source files the A2A profile pins (docs/NATIVE-CONSUMER-CI.md).
SOURCES = (
    "sdk-source.json",
    "sdk-source-pins.json",
    "sdk-license.txt",
    "local-source.json",
    "rubric.json",
    "policy.json",
    "runtime.json",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def describe(checkout: Path) -> dict:
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from probity_observer.native_reader import main; sys.exit(main())",
            "--source-checkout",
            str(checkout),
            "--describe-sources",
            PROFILE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True, help="retained packet directory")
    parser.add_argument("--checkout", type=Path, required=True, help="selected reader checkout")
    parser.add_argument("--reader-revision", required=True, help="40-character reader commit")
    parser.add_argument("--selection-id", required=True)
    parser.add_argument("--out", type=Path, required=True, help="manifest path outside the packet")
    args = parser.parse_args()

    packet = args.packet.resolve()
    out = args.out.resolve()
    if packet == out or packet in out.parents:
        raise SystemExit("selection must be written outside the retained packet")

    report = json.loads((packet / "report.json").read_text())
    artifacts = json.loads((packet / "selected-native-pins.json").read_text())
    common = packet / "common"

    missing = [name for name in SOURCES if not (common / name).is_file()]
    if missing:
        raise SystemExit(f"packet is missing retained sources: {missing}")
    source_map = {name: sha((common / name).read_bytes()) for name in SOURCES}
    sources_sha256 = sha(
        json.dumps(source_map, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )

    described = describe(args.checkout)
    manifest = {
        "format": FORMAT,
        "profile": PROFILE,
        "selection_id": args.selection_id,
        "reader_revision": args.reader_revision,
        "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "reader_sources": described["reader_sources"],
        "installed_sources": described["installed_sources"],
        "native_pins": {
            "artifacts": artifacts,
            "plan_sha256": report["plan_sha256"],
            "history_sha256": report["history_sha256"],
            "sources_sha256": sources_sha256,
        },
    }
    raw = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
    out.write_bytes(raw)
    print(
        json.dumps(
            {
                "selection": str(out),
                "sha256": sha(raw),
                "artifacts": len(artifacts),
                "python_minor": manifest["python_minor"],
                "disclosure": "reproduction-grade selection derived from the producer's "
                "retained declarations",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
