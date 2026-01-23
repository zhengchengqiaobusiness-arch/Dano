"""
基础智能体示例 - 演示如何使用自定义 ChatAgent
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from camel.messages import BaseMessage
from src.agents import CustomChatAgent, create_custom_model
from src.utils.config_loader import load_config

# 加载配置
config = load_config()
print(f"✓ 使用模型: {config['llm']['model_name']}")
print(f"✓ API Base: {config['llm']['base_url']}")


def main():
    """主函数"""
    print("=" * 60)
    print("CAMEL-AI Custom Agent Example")
    print("=" * 60)
    
    # 创建模型
    model = create_custom_model(
        model_name=config['llm']['model_name'],
        api_key=config['llm']['api_key'],
        base_url=config['llm']['base_url'],
        temperature=config['llm']['temperature']
    )
    
    # 创建自定义智能体
    agent = CustomChatAgent(
        model=model,
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
