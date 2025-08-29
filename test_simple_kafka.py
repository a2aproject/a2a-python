"""简单的 Kafka 传输测试。"""

import sys
import asyncio
sys.path.append('src')

from a2a.client.transports.kafka import KafkaClientTransport
from a2a.types import AgentCard, AgentCapabilities, AgentSkill, MessageSendParams

async def test_kafka_client():
    """测试 Kafka 客户端创建。"""
    print("测试 Kafka 客户端创建...")
    
    # 创建智能体卡片
    agent_card = AgentCard(
        name="测试智能体",
        description="测试智能体",
        url="https://example.com/test-agent",
        version="1.0.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="test_skill",
                name="test_skill",
                description="测试技能",
                tags=["test"],
                input_modes=["text/plain"],
                output_modes=["text/plain"]
            )
        ]
    )
    
    # 创建 Kafka 客户端传输
    transport = KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers="localhost:9092",
        request_topic="a2a-requests"
    )
    
    print(f"Kafka 客户端创建成功")
    print(f"   回复主题: {transport.reply_topic}")
    print(f"   消费者组: {transport.consumer_group_id}")
    
    # 测试消息参数创建
    message_params = MessageSendParams(
        content="测试消息",
        role="user"
    )
    print(f"消息参数创建成功: {message_params.content}")
    
    print("所有测试通过！")

if __name__ == "__main__":
    asyncio.run(test_kafka_client())
