"""
基础智能体示例 - 演示如何使用自定义 ChatAgent
"""

import os
from dotenv import load_dotenv
from camel.messages import BaseMessage
from camel.types import ModelType

from src.agents import CustomChatAgent

# 加载环境变量
load_dotenv()


def main():
    """主函数"""
    print("=" * 60)
    print("CAMEL-AI Custom Agent Example")
    print("=" * 60)
    
    # 创建自定义智能体
    agent = CustomChatAgent(
        model_type=ModelType.GPT_4_TURBO,
        custom_prefix="[专业助手]"
    )
    
    print("\n✓ Created CustomChatAgent with custom prefix")
    
    # 创建测试消息
    messages = [
        "What are the key principles of clean code?",
        "How can I improve my Python programming skills?",
        "Explain the concept of design patterns."
    ]
    
    # 发送消息并获取响应
    for i, msg_content in enumerate(messages, 1):
        print(f"\n{'─' * 60}")
        print(f"Message {i}: {msg_content}")
        print(f"{'─' * 60}")
        
        message = BaseMessage.make_user_message(
            role_name="User",
            content=msg_content
        )
        
        # 调用智能体
        response = agent.step(message)
        
        print(f"\nResponse:")
        print(response.msgs[0].content if hasattr(response, 'msgs') else response)
    
    # 显示统计信息
    print(f"\n{'=' * 60}")
    print("Statistics:")
    print(f"{'=' * 60}")
    stats = agent.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    # 显示消息历史
    print(f"\n{'=' * 60}")
    print("Message History:")
    print(f"{'=' * 60}")
    history = agent.get_message_history()
    for i, entry in enumerate(history, 1):
        print(f"  {i}. {entry['type'].upper()} at {entry['timestamp']}")


if __name__ == "__main__":
    main()
