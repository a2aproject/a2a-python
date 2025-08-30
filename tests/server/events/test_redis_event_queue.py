import asyncio
import json
import pytest

from a2a.server.events.redis_event_queue import RedisEventQueue


class FakeRedis:
    """Minimal fake redis supporting xadd, xread, set, delete for tests."""

    def __init__(self):
        # stream_key -> list of (id_str, fields_dict)
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        # stream_key -> next_id
        self.next_ids: dict[str, int] = {}

    async def xadd(
        self, stream_key: str, fields: dict, maxlen: int | None = None, **kwargs
    ):
        lst = self.streams.setdefault(stream_key, [])
        next_id = self.next_ids.get(stream_key, 1)
        entry_id = f'{next_id}-0'
        lst.append((entry_id, fields.copy()))
        self.next_ids[stream_key] = next_id + 1

        # Implement maxlen by trimming the list if needed
        if maxlen is not None and len(lst) > maxlen:
            # Keep only the last maxlen entries
            self.streams[stream_key] = lst[-maxlen:]

        # return id similar to real redis
        return entry_id

    async def xread(
        self, streams: dict, block: int = 0, count: int | None = None
    ):
        # streams is {stream_key: last_id}
        results = []
        for key, last_id in streams.items():
            lst = self.streams.get(key, [])
            # determine numeric last id
            if last_id == '$':
                # interpret as current max id so return only entries added after this call
                if lst:
                    last_num = max(int(eid.split('-')[0]) for eid, _ in lst)
                else:
                    last_num = 0
            else:
                try:
                    last_num = int(str(last_id).split('-')[0])
                except Exception:
                    last_num = 0

            # collect entries with numeric id > last_num
            to_return = [
                (eid, fields)
                for (eid, fields) in lst
                if int(eid.split('-')[0]) > last_num
            ]
            if to_return:
                results.append(
                    (key, to_return[: count if count is not None else None])
                )

        return results

    async def set(self, key: str, value: str):
        # no-op for tests
        return True

    async def delete(self, key: str):
        self.streams.pop(key, None)
        return True

    async def xrevrange(
        self, stream_key: str, start: str, end: str, count: int | None = None
    ):
        """Simulate Redis XREVRANGE command - get entries in reverse order."""
        lst = self.streams.get(stream_key, [])
        if not lst:
            return []

        # Return the last 'count' entries in reverse order
        to_return = lst[-count:] if count else lst
        return [(entry_id, fields) for entry_id, fields in reversed(to_return)]


class MessageEvent:
    """Dummy event with class name 'Message' and json() method."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return json.dumps(self._payload)


@pytest.mark.asyncio
async def test_enqueue_dequeue_roundtrip():
    redis = FakeRedis()
    q = RedisEventQueue(
        'task1', redis, stream_prefix='a2a:test', read_block_ms=10
    )
    evt = MessageEvent({'x': 1})
    await q.enqueue_event(evt)
    out = await q.dequeue_event(no_wait=True)
    assert out == {'x': 1}


@pytest.mark.asyncio
async def test_dequeue_no_wait_raises_on_empty():
    redis = FakeRedis()
    q = RedisEventQueue(
        'task2', redis, stream_prefix='a2a:test', read_block_ms=10
    )
    with pytest.raises(asyncio.QueueEmpty):
        await q.dequeue_event(no_wait=True)


@pytest.mark.asyncio
async def test_close_tombstone_sets_closed_and_raises():
    redis = FakeRedis()
    q = RedisEventQueue(
        'task3', redis, stream_prefix='a2a:test', read_block_ms=10
    )
    await q.enqueue_event(MessageEvent({'a': 1}))
    # close will append a CLOSE entry
    await q.close()
    # first dequeue should return the first event
    first = await q.dequeue_event(no_wait=True)
    assert first == {'a': 1}
    # next dequeue should see CLOSE and raise QueueEmpty while marking closed
    with pytest.raises(asyncio.QueueEmpty):
        await q.dequeue_event(no_wait=True)
    assert q.is_closed()


@pytest.mark.asyncio
async def test_tap_sees_only_future_events():
    redis = FakeRedis()
    q1 = RedisEventQueue(
        'task4', redis, stream_prefix='a2a:test', read_block_ms=10
    )
    # enqueue before tap
    await q1.enqueue_event(MessageEvent({'before': True}))
    # create tap which should start at '$' and only see future events
    q2 = q1.tap()
    # q1 can dequeue the earlier event
    e1 = await q1.dequeue_event(no_wait=True)
    assert e1 == {'before': True}
    # enqueue another event; both q1 and q2 should be able to read it (q1 hasn't advanced past it yet)
    await q1.enqueue_event(MessageEvent({'later': 2}))
    out2 = await q2.dequeue_event(no_wait=True)
    assert out2 == {'later': 2}


@pytest.mark.asyncio
async def test_enqueue_dequeue_with_complex_data():
    """Test enqueuing and dequeuing complex data structures."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task5', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    complex_data = {
        'nested': {'key': 'value', 'number': 42},
        'array': [1, 2, {'complex': 'item'}],
        'boolean': True,
        'null_value': None,
    }

    evt = MessageEvent(complex_data)
    await q.enqueue_event(evt)
    out = await q.dequeue_event(no_wait=True)
    assert out == complex_data


@pytest.mark.asyncio
async def test_enqueue_dequeue_with_unicode_data():
    """Test enqueuing and dequeuing Unicode strings."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task6', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    unicode_data = {
        'emoji': '🚀🌟',
        'multilingual': 'Hello 世界 नमस्ते',
        'special_chars': 'café résumé naïve',
    }

    evt = MessageEvent(unicode_data)
    await q.enqueue_event(evt)
    out = await q.dequeue_event(no_wait=True)
    assert out == unicode_data


@pytest.mark.asyncio
async def test_multiple_events_fifo_order():
    """Test that events are dequeued in FIFO order."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task7', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Enqueue multiple events
    events = [{'id': i, 'data': f'value_{i}'} for i in range(5)]
    for event_data in events:
        await q.enqueue_event(MessageEvent(event_data))

    # Dequeue and verify order
    for expected in events:
        actual = await q.dequeue_event(no_wait=True)
        assert actual == expected


@pytest.mark.asyncio
async def test_task_done_noop():
    """Test that task_done is a no-op for Redis streams."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task8', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Should not raise any exceptions
    q.task_done()


@pytest.mark.asyncio
async def test_tap_creates_independent_cursor():
    """Test that tap creates a queue with independent read cursor."""
    redis = FakeRedis()
    q1 = RedisEventQueue(
        'task9', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Enqueue some events
    await q1.enqueue_event(MessageEvent({'event': 1}))
    await q1.enqueue_event(MessageEvent({'event': 2}))

    # Create tap - this should start after the current events
    q2 = q1.tap()

    # q1 should still be able to read the events
    e1_from_q1 = await q1.dequeue_event(no_wait=True)
    e2_from_q1 = await q1.dequeue_event(no_wait=True)

    assert e1_from_q1 == {'event': 1}
    assert e2_from_q1 == {'event': 2}

    # q2 should not see the previous events (it starts at the end)
    # Enqueue a new event that both should see
    await q1.enqueue_event(MessageEvent({'event': 3}))

    # q2 should be able to read the new event
    e3_from_q2 = await q2.dequeue_event(no_wait=True)
    assert e3_from_q2 == {'event': 3}


@pytest.mark.asyncio
async def test_close_behavior():
    """Test close operation behavior."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task10', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Enqueue an event
    await q.enqueue_event(MessageEvent({'test': 'data'}))

    # Close the queue
    await q.close()

    # Should be able to dequeue existing events
    result = await q.dequeue_event(no_wait=True)
    assert result == {'test': 'data'}

    # Further dequeue should raise QueueEmpty
    with pytest.raises(asyncio.QueueEmpty):
        await q.dequeue_event(no_wait=True)


@pytest.mark.asyncio
async def test_enqueue_after_close_ignored():
    """Test that enqueuing after close is ignored."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task11', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Close first
    await q.close()

    # Enqueue should be ignored (no exception, but no effect)
    await q.enqueue_event(MessageEvent({'should_be_ignored': True}))

    # Dequeue should raise QueueEmpty immediately
    with pytest.raises(asyncio.QueueEmpty):
        await q.dequeue_event(no_wait=True)


@pytest.mark.asyncio
async def test_maxlen_parameter():
    """Test maxlen parameter limits stream size."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task12', redis, stream_prefix='a2a:test', read_block_ms=10, maxlen=2
    )

    # Add more events than maxlen
    for i in range(5):
        await q.enqueue_event(MessageEvent({'event': i}))

    # Check what's actually in the stream
    stream_key = 'a2a:test:task12'
    print(f'Stream contents: {redis.streams.get(stream_key, [])}')

    # Should only be able to dequeue the last 2 events (due to maxlen=2)
    events_dequeued = []
    try:
        while True:
            event = await q.dequeue_event(no_wait=True)
            events_dequeued.append(event)
            print(f'Dequeued event: {event}')
    except asyncio.QueueEmpty:
        pass

    print(f'Total events dequeued: {len(events_dequeued)}')

    # Should have exactly 2 events (the last 2 added due to maxlen=2)
    assert len(events_dequeued) == 2
    # Verify they are the last 2 events (events 3 and 4)
    assert events_dequeued[0]['event'] == 3
    assert events_dequeued[1]['event'] == 4


@pytest.mark.asyncio
async def test_enqueue_event_after_close_logs_warning():
    """Test that enqueuing after close logs a warning but doesn't raise."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task13', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Close the queue
    await q.close()

    # Enqueue should not raise but should log warning
    await q.enqueue_event(MessageEvent({'test': 'data'}))

    # Verify no events were actually added to stream
    stream_key = 'a2a:test:task13'
    stream_entries = redis.streams.get(stream_key, [])
    # Should only have the CLOSE entry
    assert len(stream_entries) == 1
    assert stream_entries[0][1]['type'] == 'CLOSE'


@pytest.mark.asyncio
async def test_dequeue_event_on_closed_queue_raises_immediately():
    """Test that dequeue on closed queue raises immediately."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task14', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Close without any events
    await q.close()

    # Dequeue should raise immediately
    with pytest.raises(asyncio.QueueEmpty, match='Queue is closed'):
        await q.dequeue_event(no_wait=True)


@pytest.mark.asyncio
async def test_clear_events_deletes_stream():
    """Test clear_events deletes the underlying Redis stream."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task15', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Add some events
    await q.enqueue_event(MessageEvent({'test': 1}))
    await q.enqueue_event(MessageEvent({'test': 2}))

    # Verify stream exists
    stream_key = 'a2a:test:task15'
    assert stream_key in redis.streams

    # Clear events
    await q.clear_events()

    # Verify stream is deleted
    assert stream_key not in redis.streams


@pytest.mark.asyncio
async def test_clear_events_handles_redis_error():
    """Test clear_events handles Redis errors gracefully."""

    class FailingRedis(FakeRedis):
        async def delete(self, key: str):
            raise Exception('Redis delete failed')

    redis = FailingRedis()
    q = RedisEventQueue(
        'task16', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Should not raise exception even if Redis delete fails
    await q.clear_events()


@pytest.mark.asyncio
async def test_close_idempotent():
    """Test that close() can be called multiple times safely."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task17', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Close multiple times
    await q.close()
    await q.close()
    await q.close()

    # Should only have one CLOSE entry
    stream_key = 'a2a:test:task17'
    stream_entries = redis.streams.get(stream_key, [])
    close_entries = [
        entry for entry in stream_entries if entry[1].get('type') == 'CLOSE'
    ]
    assert len(close_entries) == 1


@pytest.mark.asyncio
async def test_close_handles_redis_error():
    """Test close handles Redis errors gracefully."""

    class FailingRedis(FakeRedis):
        async def xadd(
            self,
            stream_key: str,
            fields: dict,
            maxlen: int | None = None,
            **kwargs,
        ):
            if fields.get('type') == 'CLOSE':
                raise Exception('Redis xadd failed')
            return await super().xadd(stream_key, fields, maxlen, **kwargs)

    redis = FailingRedis()
    q = RedisEventQueue(
        'task18', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Should not raise exception even if Redis xadd fails
    await q.close()


@pytest.mark.asyncio
async def test_dequeue_handles_malformed_json():
    """Test dequeue handles malformed JSON gracefully."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task19', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Manually add malformed JSON to stream
    stream_key = 'a2a:test:task19'
    redis.streams[stream_key] = [
        ('1-0', {'type': 'Message', 'payload': '{invalid json'})
    ]

    # Should return the raw malformed data
    result = await q.dequeue_event(no_wait=True)
    assert result == '{invalid json'


@pytest.mark.asyncio
async def test_dequeue_handles_unknown_event_type():
    """Test dequeue handles unknown event types gracefully."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task20', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Add event with unknown type
    await redis.xadd(
        'a2a:test:task20',
        {'type': 'UnknownType', 'payload': '{"test": "data"}'},
    )

    # Should return raw payload for unknown types
    result = await q.dequeue_event(no_wait=True)
    assert result == {'test': 'data'}


@pytest.mark.asyncio
async def test_dequeue_handles_bytes_fields():
    """Test dequeue handles Redis returning bytes for field keys/values."""

    class BytesRedis(FakeRedis):
        async def xread(
            self, streams: dict, block: int = 0, count: int | None = None
        ):
            # Call parent to get normal results
            results = await super().xread(streams, block, count)
            if results:
                # Convert some fields to bytes to simulate Redis behavior
                key, entries = results[0]
                modified_entries = []
                for entry_id, fields in entries:
                    modified_fields = {}
                    for k, v in fields.items():
                        # Convert keys and string values to bytes
                        k_bytes = k.encode('utf-8') if isinstance(k, str) else k
                        if isinstance(v, str):
                            v_bytes = v.encode('utf-8')
                        else:
                            v_bytes = v
                        modified_fields[k_bytes] = v_bytes
                    modified_entries.append((entry_id, modified_fields))
                return [(key, modified_entries)]
            return results

    redis = BytesRedis()
    q = RedisEventQueue(
        'task21', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Add an event
    await q.enqueue_event(MessageEvent({'test': 'data'}))

    # Should handle bytes conversion properly
    result = await q.dequeue_event(no_wait=True)
    assert result == {'test': 'data'}


@pytest.mark.asyncio
async def test_dequeue_handles_unicode_decode_error():
    """Test dequeue handles Unicode decode errors gracefully."""

    class BadBytesRedis(FakeRedis):
        async def xread(
            self, streams: dict, block: int = 0, count: int | None = None
        ):
            results = await super().xread(streams, block, count)
            if results:
                key, entries = results[0]
                modified_entries = []
                for entry_id, fields in entries:
                    modified_fields = {}
                    for k, v in fields.items():
                        k_bytes = k.encode('utf-8') if isinstance(k, str) else k
                        if isinstance(v, str):
                            # Create invalid UTF-8 bytes
                            v_bytes = b'\xff\xfe\xfd'
                        else:
                            v_bytes = v
                        modified_fields[k_bytes] = v_bytes
                    modified_entries.append((entry_id, modified_fields))
                return [(key, modified_entries)]
            return results

    redis = BadBytesRedis()
    q = RedisEventQueue(
        'task22', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Add an event
    await q.enqueue_event(MessageEvent({'test': 'data'}))

    # Should handle decode error and return raw bytes
    result = await q.dequeue_event(no_wait=True)
    assert result == b'\xff\xfe\xfd'


@pytest.mark.asyncio
async def test_tap_with_empty_stream():
    """Test tap behavior with empty stream."""
    redis = FakeRedis()
    q1 = RedisEventQueue(
        'task23', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Create tap on empty stream
    q2 = q1.tap()

    # Both should handle empty stream gracefully
    with pytest.raises(asyncio.QueueEmpty):
        await q1.dequeue_event(no_wait=True)

    with pytest.raises(asyncio.QueueEmpty):
        await q2.dequeue_event(no_wait=True)


@pytest.mark.asyncio
async def test_tap_with_existing_events():
    """Test tap starts after existing events."""
    redis = FakeRedis()
    q1 = RedisEventQueue(
        'task24', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Add events before tap
    await q1.enqueue_event(MessageEvent({'before': 1}))
    await q1.enqueue_event(MessageEvent({'before': 2}))

    # Create tap
    q2 = q1.tap()

    # Add event after tap
    await q1.enqueue_event(MessageEvent({'after': 3}))

    # q1 should see all events
    assert await q1.dequeue_event(no_wait=True) == {'before': 1}
    assert await q1.dequeue_event(no_wait=True) == {'before': 2}
    assert await q1.dequeue_event(no_wait=True) == {'after': 3}

    # q2 should only see the event added after tap
    assert await q2.dequeue_event(no_wait=True) == {'after': 3}


@pytest.mark.asyncio
async def test_is_closed_initially_false():
    """Test is_closed returns False initially."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task25', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    assert not q.is_closed()


@pytest.mark.asyncio
async def test_is_closed_after_close():
    """Test is_closed returns True after close."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task26', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    await q.close()

    assert q.is_closed()


@pytest.mark.asyncio
async def test_dequeue_with_blocking_timeout():
    """Test dequeue with blocking behavior (no_wait=False)."""
    redis = FakeRedis()
    q = RedisEventQueue(
        'task27', redis, stream_prefix='a2a:test', read_block_ms=10
    )

    # Add an event first
    await q.enqueue_event(MessageEvent({'immediate': 'event'}))

    # This should return the event immediately
    result = await q.dequeue_event(no_wait=False)
    assert result == {'immediate': 'event'}

    # Now test with no events - should raise QueueEmpty after timeout
    with pytest.raises(asyncio.QueueEmpty):
        await q.dequeue_event(no_wait=False)
