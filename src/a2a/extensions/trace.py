from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from a2a._base import A2ABaseModel
from a2a.extensions.base import Extension


class CallTypeEnum(str, Enum):
    """The type of the operation a step represents."""

    AGENT = 'AGENT'
    TOOL = 'TOOL'


class ToolInvocation(A2ABaseModel):
    """A tool invocation."""

    tool_name: str
    parameters: dict[str, Any]


class AgentInvocation(A2ABaseModel):
    """An agent invocation."""

    agent_url: str
    agent_name: str
    requests: dict[str, Any]
    response_trace: ResponseTrace | None = None


class StepAction(A2ABaseModel):
    """The action of a step."""

    tool_invocation: ToolInvocation | None = None
    agent_invocation: AgentInvocation | None = None


class Step(A2ABaseModel):
    """A single operation within a trace."""

    step_id: str
    trace_id: str
    parent_step_id: str | None = None
    call_type: CallTypeEnum
    step_action: StepAction
    cost: int | None = None
    total_tokens: int | None = None
    additional_attributes: dict[str, str] | None = None
    latency: int | None = None
    start_time: datetime
    end_time: datetime | None = None


class ResponseTrace(A2ABaseModel):
    """A trace message that contains a collection of spans."""

    trace_id: str
    steps: list[Step]


class TraceExtension(Extension):
    """An extension for traceability."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.traces: dict[str, ResponseTrace] = {}
        self._current_steps: dict[str, Step] = {}

    def _generate_id(self, prefix: str) -> str:
        return f'{prefix}-{uuid.uuid4()}'

    def start_trace(self) -> ResponseTrace:
        """Starts a new trace."""
        trace_id = self._generate_id('trace')
        trace = ResponseTrace(trace_id=trace_id, steps=[])
        self.traces[trace_id] = trace
        return trace

    def start_step(
        self,
        trace_id: str,
        parent_step_id: str | None,
        call_type: CallTypeEnum,
        step_action: StepAction,
    ) -> Step:
        """Starts a new step."""
        step_id = self._generate_id('step')
        step = Step(
            step_id=step_id,
            trace_id=trace_id,
            parent_step_id=parent_step_id,
            call_type=call_type,
            step_action=step_action,
            start_time=datetime.now(timezone.utc),
        )
        self._current_steps[step_id] = step
        return step

    def end_step(
        self,
        step_id: str,
        cost: int | None = None,
        total_tokens: int | None = None,
        additional_attributes: dict[str, str] | None = None,
    ) -> None:
        """Ends a step."""
        if step_id not in self._current_steps:
            return

        step = self._current_steps.pop(step_id)
        step.end_time = datetime.now(timezone.utc)
        step.latency = int(
            (step.end_time - step.start_time).total_seconds() * 1000
        )
        step.cost = cost
        step.total_tokens = total_tokens
        step.additional_attributes = additional_attributes

        if step.trace_id in self.traces:
            self.traces[step.trace_id].steps.append(step)

    def on_client_message(self, message: Any) -> None:
        """Appends trace information to the message."""
        trace = self.start_trace()
        if message.metadata is None:
            message.metadata = {}
        message.metadata['trace'] = trace.model_dump(mode='json')

    def on_server_message(self, message: Any) -> None:
        """Processes trace information from the message."""
        if (
            hasattr(message, 'metadata')
            and message.metadata is not None
            and 'trace' in message.metadata
        ):
            trace_data = message.metadata['trace']
            trace = ResponseTrace.model_validate(trace_data)
            self.traces[trace.trace_id] = trace


AgentInvocation.model_rebuild()
