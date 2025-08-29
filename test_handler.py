#!/usr/bin/env python3
"""测试 ExampleRequestHandler 是否正确实现了所有抽象方法。"""

import asyncio
import sys
import os

# 添加 src 目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from kafka_example import ExampleRequestHandler
from a2a.types import MessageSendParams, Message


async def test_handler():
    """测试请求处理器是否可以正常实例化和调用。"""
    print("测试 ExampleRequestHandler...")
    
    # 尝试实例化处理器
    try:
        handler = ExampleRequestHandler()
        print("✓ 成功实例化 ExampleRequestHandler")
    except Exception as e:
        print(f"✗ 实例化失败: {e}")
        return False
    
    # 测试消息发送
    try:
        params = MessageSendParams(
            content="测试消息",
            role="user"
        )
        response = await handler.on_message_send(params)
        print(f"✓ on_message_send 正常工作: {response.content}")
    except Exception as e:
        print(f"✗ on_message_send 失败: {e}")
        return False
    
    # 测试流式消息发送
    try:
        params = MessageSendParams(
            content="测试流式消息",
            role="user"
        )
        events = []
        async for event in handler.on_message_send_stream(params):
            events.append(event)
            print(f"✓ 收到流式事件: {event.content}")
        print(f"✓ on_message_send_stream 正常工作，收到 {len(events)} 个事件")
    except Exception as e:
        print(f"✗ on_message_send_stream 失败: {e}")
        return False
    
    print("✓ 所有测试通过!")
    return True


if __name__ == "__main__":
    success = asyncio.run(test_handler())
    sys.exit(0 if success else 1)
