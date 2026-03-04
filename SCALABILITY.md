# Scaling A2A Agents on AWS

This guide shows how to evolve a single-instance A2A agent (like the
[helloworld sample](https://github.com/a2aproject/a2a-samples/tree/main/samples/python/agents/helloworld))
into a **production-ready, horizontally-scalable deployment** on AWS using the
SDK's distributed components.

---

## 🏗️ Architecture Overview

A single-instance agent keeps all task state and event queues in process memory.
This works well for development but breaks under horizontal scaling: when a load
balancer routes a streaming client to **Instance B** while the task is running on
**Instance A**, the client never receives its events.

The distributed stack solves this with three cooperating layers:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Application Load Balancer                    │
└──────────────────────┬──────────────────┬───────────────────────┘
                       │                  │
           ┌───────────▼──────┐  ┌────────▼──────────┐
           │   ECS Instance A │  │  ECS Instance B   │
           │                  │  │                   │
           │  SnsQueueManager │  │  SnsQueueManager  │
           │  + SQS Queue A   │  │  + SQS Queue B    │
           └──────────────────┘  └───────────────────┘
                    │  ▲                  │  ▲
              publish│  │receive    publish│  │receive
                    ▼  │                  ▼  │
           ┌────────────────────────────────────────┐
           │           AWS SNS Topic                │
           │   (fan-out to all instance queues)     │
           └────────────────────────────────────────┘
                       │
           ┌───────────▼────────────────────────────┐
           │           AWS DynamoDB                 │
           │      (persistent task storage)         │
           └────────────────────────────────────────┘
```

| Component | Role |
|---|---|
| `DynamoDBTaskStore` | Replaces `InMemoryTaskStore` — persists task state across instances and restarts |
| `QueueLifecycleManager` | On ECS startup, creates a per-instance SQS queue and subscribes it to the shared SNS topic |
| `SnsQueueManager` | Replaces `InMemoryQueueManager` — publishes events to SNS and polls the local SQS queue for remote events |
| `DistributedEventQueue` | Created internally by `SnsQueueManager` — enqueues events locally **and** fans them out to SNS |

---

## 📦 Installation

```bash
# Core SDK + AWS distributed components
pip install "a2a-sdk[aws]"

# Or with uv
uv add "a2a-sdk[aws]"
```

---

## ☁️ AWS Prerequisites

The following AWS resources must exist before starting your agent. Terraform /
CloudFormation examples are shown for reference.

### DynamoDB Table

```hcl
resource "aws_dynamodb_table" "a2a_tasks" {
  name         = "a2a-tasks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "task_id"

  attribute {
    name = "task_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
```

### SNS Topic

```hcl
resource "aws_sns_topic" "a2a_events" {
  name = "a2a-events"
}
```

> **Note:** The per-instance SQS queues are created and destroyed automatically
> by `QueueLifecycleManager` at agent startup/shutdown. You do **not** need to
> pre-create them.

### IAM Policy for ECS Task Role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DynamoDBTaskStore",
      "Effect": "Allow",
      "Action": ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:DeleteItem"],
      "Resource": "arn:aws:dynamodb:*:*:table/a2a-tasks"
    },
    {
      "Sid": "SnsPublish",
      "Effect": "Allow",
      "Action": "sns:Publish",
      "Resource": "arn:aws:sns:*:*:a2a-events"
    },
    {
      "Sid": "SqsLifecycle",
      "Effect": "Allow",
      "Action": [
        "sqs:CreateQueue",
        "sqs:DeleteQueue",
        "sqs:GetQueueAttributes",
        "sqs:SetQueueAttributes",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:DeleteMessageBatch"
      ],
      "Resource": "arn:aws:sqs:*:*:a2a-instance-*"
    },
    {
      "Sid": "SnsSubscribe",
      "Effect": "Allow",
      "Action": ["sns:Subscribe", "sns:Unsubscribe"],
      "Resource": "arn:aws:sns:*:*:a2a-events"
    }
  ]
}
```

---

## 🚀 Building a Production-Ready Agent

### Step 1 — Agent Executor (unchanged from helloworld)

Your business logic remains exactly the same. The distributed stack is purely
infrastructure wiring — no changes to `AgentExecutor` are needed.

```python
# agent_executor.py
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message


class MyAgent:
    async def invoke(self, message: str) -> str:
        # Replace with your actual LLM / tool call logic.
        return f"Processed: {message}"


class MyAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.agent = MyAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        user_input = context.get_user_input()
        result = await self.agent.invoke(user_input)
        await event_queue.enqueue_event(new_agent_text_message(result))

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        raise NotImplementedError("cancel not supported")
```

### Step 2 — Resolve the Instance ID

In ECS Fargate, each task has a unique ARN available from the container metadata
endpoint. Using this as the `instance_id` ensures clean deduplication and
observability.

```python
# instance.py
import os
import httpx


async def get_instance_id() -> str:
    """Resolve a stable instance ID from ECS metadata, or fall back to a UUID."""
    metadata_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if metadata_uri:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{metadata_uri}/task", timeout=2.0)
                data = resp.json()
                # Use the short task ID portion of the ARN.
                task_arn: str = data.get("TaskARN", "")
                if task_arn:
                    return task_arn.split("/")[-1]
        except Exception:
            pass  # Fall through to UUID fallback.
    import uuid
    return str(uuid.uuid4())
```

### Step 3 — Wire Everything Together

```python
# __main__.py
import asyncio
import os

import aioboto3
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import DynamoDBTaskStore
from a2a.server.events import QueueLifecycleManager, SnsQueueManager
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from agent_executor import MyAgentExecutor
from instance import get_instance_id


# ── Configuration (inject via environment variables in ECS task definition) ──

REGION        = os.environ.get("AWS_REGION", "us-east-1")
TABLE_NAME    = os.environ.get("DYNAMODB_TABLE", "a2a-tasks")
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]   # required


async def main() -> None:
    instance_id = await get_instance_id()

    # Shared aioboto3 session — one per process, reused across all components.
    session = aioboto3.Session()

    # ── 1. Persistent task store ─────────────────────────────────────────────
    task_store = DynamoDBTaskStore(
        table_name=TABLE_NAME,
        region_name=REGION,
        session=session,
    )

    # ── 2. Per-instance SQS queue (provisioned on startup, torn down on exit) ─
    async with QueueLifecycleManager(
        topic_arn=SNS_TOPIC_ARN,
        queue_name_prefix="a2a-instance",
        instance_id=instance_id,
        region_name=REGION,
        session=session,
    ) as lifecycle:

        # lifecycle.queue_url is now populated.
        assert lifecycle.queue_url is not None

        # ── 3. Distributed queue manager ─────────────────────────────────────
        queue_manager = SnsQueueManager(
            topic_arn=SNS_TOPIC_ARN,
            sqs_queue_url=lifecycle.queue_url,
            instance_id=instance_id,
            region_name=REGION,
            session=session,
            poll_interval_seconds=1.0,
            max_messages=10,
        )
        await queue_manager.start()

        try:
            # ── 4. A2A application stack ──────────────────────────────────────
            skill = AgentSkill(
                id="my_skill",
                name="My Agent Skill",
                description="Processes user requests with horizontal scalability.",
                tags=["production"],
                examples=["hello", "process this"],
            )

            agent_card = AgentCard(
                name="My Scalable Agent",
                description="A production A2A agent backed by DynamoDB and SNS/SQS.",
                url=os.environ.get("AGENT_URL", "http://localhost:8080/"),
                version="1.0.0",
                default_input_modes=["text"],
                default_output_modes=["text"],
                capabilities=AgentCapabilities(streaming=True),
                skills=[skill],
            )

            request_handler = DefaultRequestHandler(
                agent_executor=MyAgentExecutor(),
                task_store=task_store,
                queue_manager=queue_manager,
            )

            app = A2AStarletteApplication(
                agent_card=agent_card,
                http_handler=request_handler,
            )

            config = uvicorn.Config(
                app.build(),
                host="0.0.0.0",
                port=8080,
                log_level="info",
            )
            server = uvicorn.Server(config)
            await server.serve()

        finally:
            # Always stop the SQS poller before QueueLifecycleManager tears
            # down the SQS queue. This prevents in-flight ReceiveMessage calls
            # from racing with DeleteQueue.
            await queue_manager.stop()
            # QueueLifecycleManager.teardown() is called automatically by the
            # async with block — unsubscribes from SNS and deletes the SQS queue.


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🔄 Startup and Shutdown Sequence

Understanding the boot order matters for zero-downtime deployments.

```
STARTUP
  1. Resolve instance_id (ECS task ARN or random UUID)
  2. QueueLifecycleManager.provision()
       └─ CreateQueue   → SQS queue created
       └─ GetQueueAttributes → queue ARN fetched
       └─ SetQueueAttributes → SNS→SQS access policy attached
       └─ SNS.Subscribe  → queue subscribed to SNS topic
  3. SnsQueueManager.start()
       └─ Background asyncio task begins polling SQS
  4. uvicorn begins accepting HTTP traffic
       └─ ECS registers instance as healthy in ALB target group

SHUTDOWN (SIGTERM from ECS)
  1. uvicorn stops accepting new connections; drains in-flight requests
  2. SnsQueueManager.stop()
       └─ Sets stop_event; waits for polling task to exit cleanly
  3. QueueLifecycleManager.teardown()  (via async with __aexit__)
       └─ SNS.Unsubscribe  → no more messages delivered to this instance
       └─ SQS.DeleteQueue  → per-instance queue removed
```

---

## ⚡ How Event Fan-Out Works

This sequence shows what happens when Instance A's agent produces an event and
Instance B's client is streaming that task.

```
Agent (Instance A)
  └─ AgentExecutor.execute()
       └─ event_queue.enqueue_event(event)          # DistributedEventQueue
            ├─ super().enqueue_event(event)          # local asyncio.Queue → SSE stream on A
            └─ asyncio.create_task(_publish_event)  # fire-and-forget SNS publish

SNS Topic
  └─ fan-out to all subscribed SQS queues (A and B)

SQS Poller (Instance B)
  └─ _poll_loop() receives SQS message
       └─ deserialize_wire_message()
       └─ deduplicate: skip if instance_id == self._instance_id   ← A's message passes B's check
       └─ queue.enqueue_local(event)                               ← no re-publish to SNS
            └─ local asyncio.Queue → SSE stream on B ✓
```

---

## 🗂️ DynamoDB Task Store Details

`DynamoDBTaskStore` serializes each `Task` as a Pydantic JSON string under a
single `task_data` attribute. The table schema is intentionally minimal:

| Attribute | DynamoDB Type | Description |
|---|---|---|
| `task_id` | String (Partition Key) | The A2A task ID |
| `task_data` | String | Full `Task` serialized as JSON |

**Consistent reads** are used for `get()` to avoid stale reads after a task has
been updated on another instance. This trades a small amount of read throughput
for strong consistency guarantees required by the A2A protocol.

```python
# Direct usage (without the full server stack)
import aioboto3
from a2a.server.tasks import DynamoDBTaskStore

session = aioboto3.Session()
store = DynamoDBTaskStore("a2a-tasks", region_name="us-east-1", session=session)

await store.save(task)
task = await store.get(task_id)
await store.delete(task_id)
```

---

## ⚙️ Configuration Reference

### `DynamoDBTaskStore`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `table_name` | `str` | required | DynamoDB table name |
| `region_name` | `str` | `'us-east-1'` | AWS region |
| `session` | `aioboto3.Session \| None` | `None` | Shared session; a new one is created if omitted |

### `QueueLifecycleManager`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `topic_arn` | `str` | required | ARN of the SNS topic to subscribe to |
| `queue_name_prefix` | `str` | `'a2a-instance'` | SQS queue name prefix; suffix is `instance_id` |
| `instance_id` | `str` | random UUID | Unique ID for this process |
| `region_name` | `str` | `'us-east-1'` | AWS region |
| `session` | `aioboto3.Session \| None` | `None` | Shared session |

### `SnsQueueManager`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `topic_arn` | `str` | required | ARN of the shared SNS topic |
| `sqs_queue_url` | `str` | required | URL of this instance's SQS queue (from `QueueLifecycleManager`) |
| `instance_id` | `str \| None` | `None` → random UUID | Must match the ID used in `QueueLifecycleManager` |
| `region_name` | `str` | `'us-east-1'` | AWS region |
| `session` | `aioboto3.Session \| None` | `None` | Shared session |
| `poll_interval_seconds` | `float` | `1.0` | Sleep between SQS polling cycles |
| `max_messages` | `int` | `10` | Max messages per `ReceiveMessage` call (1–10) |
| `visibility_timeout_seconds` | `int` | `30` | SQS visibility timeout per polling call |

---

## 🔍 Observability

All four components emit structured log messages using Python's standard
`logging` module under their fully-qualified module names:

| Logger | Key events |
|---|---|
| `a2a.server.tasks.dynamodb_task_store` | save / get / delete per task ID |
| `a2a.server.events.queue_lifecycle_manager` | provision, subscribe, teardown, rollback |
| `a2a.server.events.sns_queue_manager` | start/stop, per-message routing, deduplication |
| `a2a.server.events.distributed_event_queue` | publish to SNS, local delivery |

Configure at `INFO` level in production and `DEBUG` for detailed per-message
tracing:

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# Per-component debug without flooding the root logger:
logging.getLogger("a2a.server.events.sns_queue_manager").setLevel(logging.DEBUG)
```

For OpenTelemetry tracing, install the telemetry extra and configure the SDK
before starting the server:

```bash
pip install "a2a-sdk[aws,telemetry]"
```

---

## 🛡️ Production Checklist

- [ ] DynamoDB table created with `task_id` (String) as partition key
- [ ] SNS topic created; ARN exported as `SNS_TOPIC_ARN` environment variable
- [ ] ECS task role has the IAM policy shown above
- [ ] `instance_id` resolved from ECS task metadata (not random UUID in prod)
- [ ] One shared `aioboto3.Session` passed to all three components
- [ ] `QueueLifecycleManager` used as `async with` — guarantees teardown on SIGTERM
- [ ] `SnsQueueManager.stop()` called in `finally` block before context exit
- [ ] ALB stickiness **disabled** — the distributed stack handles cross-instance routing
- [ ] ECS task `stopTimeout` set ≥ 30 s to allow graceful SQS drain and queue deletion
- [ ] DynamoDB point-in-time recovery (PITR) enabled for task durability
- [ ] SNS dead-letter queue (DLQ) configured for undeliverable messages

---

## 🔗 Related

- [A2A Python SDK — README](README.md)
- [A2A Protocol Specification](https://a2a-protocol.org)
- [Helloworld Sample Agent](https://github.com/a2aproject/a2a-samples/tree/main/samples/python/agents/helloworld)
- [a2a-samples Repository](https://github.com/a2aproject/a2a-samples)
- [aioboto3 Documentation](https://aioboto3.readthedocs.io)
