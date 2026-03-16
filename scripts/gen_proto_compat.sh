#!/bin/bash
set -e

# Generate legacy v0.3 compatibility protobuf code
echo "Generating legacy v0.3 compatibility protobuf code"
npx --yes @bufbuild/buf generate src/a2a/compat/v0_3 --template buf.compat.gen.yaml

# Fix imports in legacy generated grpc file
echo "Fixing imports in src/a2a/compat/v0_3/a2a_v0_3_pb2_grpc.py"
sed 's/import a2a_v0_3_pb2 as a2a__v0__3__pb2/from . import a2a_v0_3_pb2 as a2a__v0__3__pb2/g' src/a2a/compat/v0_3/a2a_v0_3_pb2_grpc.py > src/a2a/compat/v0_3/a2a_v0_3_pb2_grpc.py.tmp && mv src/a2a/compat/v0_3/a2a_v0_3_pb2_grpc.py.tmp src/a2a/compat/v0_3/a2a_v0_3_pb2_grpc.py
