#!/bin/bash
set -ex

# 1. Pull a2a-samples and checkout branch
A2A_SAMPLES_BRANCH=${A2A_SAMPLES_BRANCH:-implement-itk-service}

if [ ! -d "a2a-samples" ]; then
  git clone https://github.com/a2aproject/a2a-samples.git a2a-samples
fi
cd a2a-samples
git fetch origin
git checkout "$A2A_SAMPLES_BRANCH"
git pull origin "$A2A_SAMPLES_BRANCH"
cd ..

# 2. Copy instruction.proto from a2a-samples
cp a2a-samples/itk/protos/instruction.proto ./instruction.proto

# 3. Build pyproto library
mkdir -p pyproto
touch pyproto/__init__.py
uv run --with grpcio-tools python -m grpc_tools.protoc \
    -I. \
    --python_out=pyproto \
    --grpc_python_out=pyproto \
    instruction.proto

# Fix imports in generated file
sed -i 's/^import instruction_pb2 as instruction__pb2/from . import instruction_pb2 as instruction__pb2/' pyproto/instruction_pb2_grpc.py

# 4. Build jit itk_service docker image from root of a2a-samples/itk
# We run docker build from the itk directory inside a2a-samples
docker build -t itk_service a2a-samples/itk

# 5. Start docker service
# Mounting a2a-python as repo and itk as current agent
A2A_PYTHON_ROOT=$(cd .. && pwd)
ITK_DIR=$(pwd)

# Stop existing container if any
docker rm -f itk-service || true

docker run -d --name itk-service \
  -v "$A2A_PYTHON_ROOT:/app/agents/repo" \
  -v "$ITK_DIR:/app/agents/repo/itk" \
  -p 8000:8000 \
  itk_service

# 6. Verify service is up and send post request
MAX_RETRIES=30
echo "Waiting for ITK service to start on 127.0.0.1:8000..."
set +e
for i in $(seq 1 $MAX_RETRIES); do
  if curl -s http://127.0.0.1:8000/ > /dev/null; then
    echo "Service is up!"
    break
  fi
  echo "Still waiting... ($i/$MAX_RETRIES)"
  sleep 2
done

# If we reached the end of the loop without success
if ! curl -s http://127.0.0.1:8000/ > /dev/null; then
  echo "Error: ITK service failed to start on port 8000"
  docker logs itk-service
  docker rm -f itk-service
  exit 1
fi

echo "ITK Service is up! Sending compatibility test request..."
RESPONSE=$(curl -s -X POST http://127.0.0.1:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "tests": [
      {
        "name": "Star Topology (Full) - JSONRPC & GRPC",
        "sdks": ["current", "python_v10", "python_v03", "go_v10", "go_v03"],
        "traversal": "euler",
        "edges": ["0->1", "0->2", "0->3", "0->4", "1->0", "2->0", "3->0", "4->0"],
        "protocols": ["jsonrpc", "grpc"]
      },
      {
        "name": "Star Topology (No Go v03) - HTTP_JSON",
        "sdks": ["current", "python_v10", "python_v03", "go_v10"],
        "traversal": "euler",
        "edges": ["0->1", "0->2", "0->3", "1->0", "2->0", "3->0"],
        "protocols": ["http_json"]
      },
      {
        "name": "Star Topology (Full) - JSONRPC & GRPC (Streaming)",
        "sdks": ["current", "python_v10", "python_v03", "go_v10", "go_v03"],
        "traversal": "euler",
        "edges": ["0->1", "0->2", "0->3", "0->4", "1->0", "2->0", "3->0", "4->0"],
        "protocols": ["jsonrpc", "grpc"],
        "streaming": true
      },
      {
        "name": "Star Topology (No Go v03) - HTTP_JSON (Streaming)",
        "sdks": ["current", "python_v10", "python_v03", "go_v10"],
        "traversal": "euler",
        "edges": ["0->1", "0->2", "0->3", "1->0", "2->0", "3->0"],
        "protocols": ["http_json"],
        "streaming": true
      }
    ]
  }')

echo "--------------------------------------------------------"
echo "ITK TEST RESULTS:"
echo "--------------------------------------------------------"
echo "$RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    all_passed = data.get('all_passed', False)
    results = data.get('results', {})
    for test, passed in results.items():
        status = 'PASSED' if passed else 'FAILED'
        print(f'{test}: {status}')
    print('--------------------------------------------------------')
    print(f'OVERALL STATUS: {\"PASSED\" if all_passed else \"FAILED\"}')
except Exception as e:
    print(f'Error parsing results: {e}')
    print(f'Raw response: {data if \"data\" in locals() else \"no data\"}')
"
echo "--------------------------------------------------------"

# 7. Cleanup
set +x
echo "Cleaning up artifacts..."
docker stop itk-service > /dev/null 2>&1 || true
docker rm itk-service > /dev/null 2>&1 || true
docker rmi itk_service > /dev/null 2>&1 || true
rm -rf a2a-samples > /dev/null 2>&1
rm -rf pyproto > /dev/null 2>&1
rm -f instruction.proto > /dev/null 2>&1
echo "Done."
