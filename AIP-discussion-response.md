# Response to AIP Discussion #1247

> Re: [Respecting AIP response payloads in HTTP](https://github.com/a2aproject/A2A/discussions/1247)

Thanks for this detailed explanation of the AIP conventions, @darrelmiller. I've been working on the a2a-python SDK migration from Pydantic to protobuf types ([PR #572](https://github.com/a2aproject/a2a-python/pull/572)) and wanted to share how we've implemented this.

## How we handle `SetTaskPushNotificationConfig` in the SDK

The key insight is that the request and response types serve different purposes:

**Request (`SetTaskPushNotificationConfigRequest`):**
```protobuf
message SetTaskPushNotificationConfigRequest {
  string parent = 1;      // e.g., "tasks/{task_id}"
  string config_id = 2;   // e.g., "my-config-id"
  TaskPushNotificationConfig config = 3;
}
```

**Response (`TaskPushNotificationConfig`):**
```protobuf
message TaskPushNotificationConfig {
  string name = 1;  // Full resource name: "tasks/{task_id}/pushNotificationConfigs/{config_id}"
  PushNotificationConfig push_notification_config = 2;
}
```

## Implementation in Python

In our `DefaultRequestHandler`, we construct the proper `name` field from the request's `parent` and `config_id`:

```python
async def on_set_task_push_notification_config(
    self,
    params: SetTaskPushNotificationConfigRequest,
    context: ServerCallContext | None = None,
) -> TaskPushNotificationConfig:
    task_id = _extract_task_id(params.parent)  # Extract from "tasks/{task_id}"
    
    # Store the config
    await self._push_config_store.set_info(
        task_id,
        params.config.push_notification_config,
    )

    # Build response with proper AIP resource name
    return TaskPushNotificationConfig(
        name=f'{params.parent}/pushNotificationConfigs/{params.config_id}',
        push_notification_config=params.config.push_notification_config,
    )
```

## REST Handler Translation

For the HTTP binding, the REST handler extracts path parameters and constructs the request:

```python
async def set_push_notification(self, request: Request, context: ServerCallContext):
    task_id = request.path_params['id']
    body = await request.body()
    
    params = SetTaskPushNotificationConfigRequest()
    Parse(body, params)
    params.parent = f'tasks/{task_id}'  # Set from URL path
    
    config = await self.request_handler.on_set_task_push_notification_config(params, context)
    return MessageToDict(config)  # Returns with proper `name` field
```

## JSON-RPC Handler

The JSON-RPC handler passes the full request directly:

```python
async def set_push_notification_config(
    self,
    request: SetTaskPushNotificationConfigRequest,
    context: ServerCallContext | None = None,
) -> SetTaskPushNotificationConfigResponse:
    result = await self.request_handler.on_set_task_push_notification_config(
        request, context
    )
    return prepare_response_object(...)
```

## Key Takeaways

1. **The `name` field is constructed, not passed in** - The server builds the full resource name from `parent` + `config_id`

2. **Consistent across bindings** - Both gRPC and HTTP handlers ultimately call the same `on_set_task_push_notification_config` method

3. **AIP compliance** - The response always includes the full `name` field as required by [AIP-122](https://google.aip.dev/122)

4. **Helper functions for resource name parsing**:
   ```python
   def _extract_task_id(resource_name: str) -> str:
       """Extract task ID from a resource name like 'tasks/{task_id}' or 'tasks/{task_id}/...'."""
       match = re.match(r'^tasks/([^/]+)', resource_name)
       if match:
           return match.group(1)
       return resource_name  # Fall back for backwards compatibility

   def _extract_config_id(resource_name: str) -> str | None:
       """Extract config ID from 'tasks/{task_id}/pushNotificationConfigs/{config_id}'."""
       match = re.match(r'^tasks/[^/]+/pushNotificationConfigs/([^/]+)$', resource_name)
       if match:
           return match.group(1)
       return None
   ```

## E2E Test Example

Here's how a client uses this in practice:

```python
# Client sets the push notification config
await a2a_client.set_task_callback(
    SetTaskPushNotificationConfigRequest(
        parent=f'tasks/{task.id}',
        config_id='my-notification-config',
        config=TaskPushNotificationConfig(
            push_notification_config=PushNotificationConfig(
                id='my-notification-config',
                url=f'{notifications_server}/notifications',
                token=token,
            ),
        ),
    )
)
```

This approach keeps the abstract handler logic clean while ensuring AIP compliance at the protocol binding level.

---

**Related PRs:**
- [a2a-python PR #572](https://github.com/a2aproject/a2a-python/pull/572) - Proto migration with these changes
