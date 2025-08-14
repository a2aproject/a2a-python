from datetime import datetime, timezone

from a2a.extensions.trace import (
    AgentInvocation,
    CallTypeEnum,
    ResponseTrace,
    Step,
    StepAction,
    ToolInvocation,
)


def test_trace_serialization():
    start_time = datetime(2025, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2025, 3, 15, 12, 0, 0, 250000, tzinfo=timezone.utc)

    trace = ResponseTrace(
        trace_id='trace-example-12345',
        steps=[
            Step(
                step_id='step-1-agent',
                trace_id='trace-example-1234p',
                call_type=CallTypeEnum.AGENT,
                step_action=StepAction(
                    agent_invocation=AgentInvocation(
                        agent_name='weather_agent',
                        agent_url='http://google3/some/agent/url',
                        requests={
                            'user_prompt': "What's the weather in Paris and what should I wear?"
                        },
                    )
                ),
                cost=150,
                total_tokens=75,
                additional_attributes={'user_country': 'US'},
                latency=250,
                start_time=start_time,
                end_time=end_time,
            ),
            Step(
                step_id='step-2-tool',
                trace_id='trace-example-12345',
                parent_step_id='step-1-agent',
                call_type=CallTypeEnum.TOOL,
                step_action=StepAction(
                    tool_invocation=ToolInvocation(
                        tool_name='google_map_api_tool',
                        parameters={'location': 'Paris, FR'},
                    )
                ),
                cost=50,
                total_tokens=20,
                latency=100,
                start_time=start_time,
                end_time=end_time,
            ),
        ],
    )

    trace_dict = trace.model_dump(mode='json')
    deserialized_trace = ResponseTrace.model_validate(trace_dict)

    assert trace == deserialized_trace
