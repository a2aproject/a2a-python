import asyncio
import sys
import types
from dataclasses import dataclass
from typing import Any, List, Optional

import pytest
from unittest.mock import AsyncMock

# Inject a fake aiokafka module before importing the app under test
fake_aiokafka = types.ModuleType("aiokafka")
fake_aiokafka_errors = types.ModuleType("aiokafka.errors")


class FakeKafkaError(Exception):
    pass


fake_aiokafka_errors.KafkaError = FakeKafkaError


class FakeProducer:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.started = False
        self.sent: List[tuple] = []  # (topic, value, headers)

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False

    async def send_and_wait(self, topic: str, value: Any, headers: list[tuple[str, bytes]] | None = None):
        self.sent.append((topic, value, headers or []))


@dataclass
class FakeMessage:
    value: Any
    headers: Optional[List[tuple[str, bytes]]] = None


class FakeConsumer:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.started = False
        # queue of messages to yield
        self._messages: List[FakeMessage] = []

    def add_message(self, value: Any, headers: Optional[List[tuple[str, bytes]]] = None):
        self._messages.append(FakeMessage(value=value, headers=headers))

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False

    def __aiter__(self):
        self._iter = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


fake_aiokafka.AIOKafkaProducer = FakeProducer
fake_aiokafka.AIOKafkaConsumer = FakeConsumer

sys.modules.setdefault("aiokafka", fake_aiokafka)
sys.modules.setdefault("aiokafka.errors", fake_aiokafka_errors)

# Now safe to import the module under test
from a2a.server.apps.kafka.kafka_app import KafkaServerApp, KafkaHandler
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.request_handlers.kafka_handler import KafkaMessage


class DummyHandler:
    """A minimal KafkaHandler drop-in used to capture consumed messages."""

    def __init__(self):
        self.handled: list[KafkaMessage] = []

    async def handle_request(self, message: KafkaMessage) -> None:
        self.handled.append(message)


@pytest.fixture
def request_handler():
    return AsyncMock(spec=RequestHandler)


@pytest.fixture
def app(monkeypatch, request_handler):
    # Replace KafkaHandler inside the kafka_app module to our DummyHandler
    dummy = DummyHandler()

    def _fake_kafka_handler_ctor(rh, response_sender):
        # validate response_sender is the app instance later
        return dummy

    # Patch the symbol used by kafka_app
    monkeypatch.setattr(
        "a2a.server.apps.kafka.kafka_app.KafkaHandler", _fake_kafka_handler_ctor
    )

    a = KafkaServerApp(
        request_handler=request_handler,
        bootstrap_servers="dummy:9092",
        request_topic="a2a-requests",
        consumer_group_id="a2a-server",
    )
    # expose dummy for assertions
    a._dummy_handler = dummy
    return a


@pytest.mark.asyncio
async def test_start_initializes_components(app: KafkaServerApp):
    await app.start()
    assert app._running is True
    assert isinstance(app.producer, FakeProducer) and app.producer.started
    assert isinstance(app.consumer, FakeConsumer) and app.consumer.started
    # handler constructed
    assert app.handler is app._dummy_handler


@pytest.mark.asyncio
async def test_stop_closes_components(app: KafkaServerApp):
    await app.start()
    await app.stop()
    assert app._running is False
    assert app.producer is not None and app.producer.started is False
    assert app.consumer is not None and app.consumer.started is False


@pytest.mark.asyncio
async def test_send_response_uses_producer_headers_and_payload(app: KafkaServerApp):
    await app.start()
    await app.send_response("reply-topic", "corr-1", {"k": 1}, "task")
    assert len(app.producer.sent) == 1
    topic, value, headers = app.producer.sent[0]
    assert topic == "reply-topic"
    assert value["type"] == "task" and value["data"] == {"k": 1}
    assert ("correlation_id", b"corr-1") in headers


@pytest.mark.asyncio
async def test_send_stream_complete_uses_producer(app: KafkaServerApp):
    await app.start()
    await app.send_stream_complete("reply-topic", "corr-2")
    topic, value, headers = app.producer.sent[-1]
    assert topic == "reply-topic"
    assert value["type"] == "stream_complete"
    assert ("correlation_id", b"corr-2") in headers


@pytest.mark.asyncio
async def test_send_error_response_uses_producer(app: KafkaServerApp):
    await app.start()
    await app.send_error_response("reply-topic", "corr-3", "boom")
    topic, value, headers = app.producer.sent[-1]
    assert topic == "reply-topic"
    assert value["type"] == "error"
    assert value["data"]["error"] == "boom"
    assert ("correlation_id", b"corr-3") in headers


@pytest.mark.asyncio
async def test_consume_requests_converts_and_delegates(app: KafkaServerApp):
    await app.start()
    # Prepare a message for the consumer
    assert isinstance(app.consumer, FakeConsumer)
    app.consumer.add_message(
        value={"method": "message_send", "params": {}, "streaming": False},
        headers=[("reply_topic", b"replies"), ("correlation_id", b"cid-1")],
    )

    # Run consume loop once; since FakeConsumer yields finite messages, it will end
    await app._consume_requests()

    # Verify our dummy handler saw the converted KafkaMessage
    handled = app._dummy_handler.handled
    assert len(handled) == 1
    km: KafkaMessage = handled[0]
    assert km.get_header("reply_topic") == "replies"
    assert km.get_header("correlation_id") == "cid-1"
    assert km.value["method"] == "message_send"
