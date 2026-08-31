#!/bin/bash
# ITK harness for a2a-python — a thin shim over a2a-itk's shared driver.
#
# Everything that used to live here (clone, image build, container start,
# readiness poll, POST /run, result reporting, nightly metrics) is now in
# a2a-itk/scripts/run_itk_shared.sh, which all five SDK repos share. Only the
# genuinely python-specific part stays: generating the proto stubs.
#
# Scenarios come from the shared role-based set in a2a-itk rather than a
# scenarios.json in this repo — see a2a-itk/scenarios/traversal/.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

ITK_SDK_NAME=python
ITK_SCENARIO_SET=shared

itk_generate_protos() {
  mkdir -p pyproto
  touch pyproto/__init__.py
  uv run --with grpcio-tools python -m grpc_tools.protoc \
      -I. \
      --python_out=pyproto \
      --grpc_python_out=pyproto \
      instruction.proto
  # Generated code imports its sibling as a top-level module; make it relative
  # so `pyproto` works as a package.
  sed -i 's/^import instruction_pb2 as instruction__pb2/from . import instruction_pb2 as instruction__pb2/' \
      pyproto/instruction_pb2_grpc.py
}

itk_extra_cleanup() {
  rm -rf pyproto
}

# --- bootstrap -------------------------------------------------------------
# The shared driver lives in a2a-itk, so the checkout has to exist before it
# can be sourced. CI has already placed it here via actions/checkout; locally
# we clone it from a2aproject/a2a-itk.
: "${A2A_ITK_REVISION:?A2A_ITK_REVISION environment variable must be set}"
if [ ! -d a2a-itk ]; then
  git clone https://github.com/a2aproject/a2a-itk.git a2a-itk
  git -C a2a-itk checkout "$A2A_ITK_REVISION"
fi

source a2a-itk/scripts/run_itk_shared.sh
