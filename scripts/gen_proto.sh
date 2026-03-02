#!/bin/bash
set -e

# Run buf generate to regenerate protobuf code and OpenAPI spec
npx --yes @bufbuild/buf generate

# The OpenAPI generator produces a file named like 'a2a.swagger.json' or similar.
# We need it to be 'a2a.json' for the A2A SDK.
# Find the generated json file in the output directory
generated_json=$(find src/a2a/types -name "*.swagger.json" -print -quit)

if [ -n "$generated_json" ]; then
    echo "Renaming $generated_json to src/a2a/types/a2a.json"
    mv "$generated_json" src/a2a/types/a2a.json
else
    echo "Warning: No Swagger JSON generated."
fi

# Fix imports in generated grpc file
echo "Fixing imports in src/a2a/types/a2a_pb2_grpc.py"
sed 's/import a2a_pb2 as a2a__pb2/from . import a2a_pb2 as a2a__pb2/g' src/a2a/types/a2a_pb2_grpc.py > src/a2a/types/a2a_pb2_grpc.py.tmp && mv src/a2a/types/a2a_pb2_grpc.py.tmp src/a2a/types/a2a_pb2_grpc.py

# Download legacy v0.3 compatibility protobuf code
echo "Downloading legacy v0.3 proto file..."
curl -o src/a2a/compat/v0_3/a2a_v0_3.proto https://raw.githubusercontent.com/a2aproject/A2A/a8b45dcc429a5571ef8a24c36336bf84b89bbd7f/specification/grpc/a2a.proto

# Generate legacy v0.3 compatibility protobuf code
echo "Generating legacy v0.3 compatibility protobuf code"
npx --yes @bufbuild/buf generate src/a2a/compat/v0_3 --template buf.compat.gen.yaml

# Fix imports in legacy generated grpc file
echo "Fixing imports in src/a2a/compat/v0_3/a2a_v0_3_pb2_grpc.py"
sed 's/import a2a_v0_3_pb2 as a2a__v0__3__pb2/from . import a2a_v0_3_pb2 as a2a__v0__3__pb2/g' src/a2a/compat/v0_3/a2a_v0_3_pb2_grpc.py > src/a2a/compat/v0_3/a2a_v0_3_pb2_grpc.py.tmp && mv src/a2a/compat/v0_3/a2a_v0_3_pb2_grpc.py.tmp src/a2a/compat/v0_3/a2a_v0_3_pb2_grpc.py
