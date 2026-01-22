"""
专业智能体示例 - 演示如何使用针对特定领域优化的智能体
"""

import os
from dotenv import load_dotenv
from camel.types import ModelType

from src.agents import SpecializedAgent

# 加载环境变量
load_dotenv()


def main():
    """主函数"""
    print("=" * 60)
    print("CAMEL-AI Specialized Agent Example")
    print("=" * 60)
    
    # 创建不同领域的专业智能体
    domains = [
        ("programming", "expert"),
        ("data_science", "expert"),
        ("writing", "intermediate")
    ]
    
    for domain, expertise in domains:
        print(f"\n{'=' * 60}")
        print(f"Domain: {domain.upper()} | Expertise: {expertise}")
        print(f"{'=' * 60}")
        
        # 创建专业智能体
        agent = SpecializedAgent(
            domain=domain,
            expertise_level=expertise,
            model_type=ModelType.GPT_4_TURBO
        )
        
        # 准备任务描述
        tasks = {
            'programming': "Build a REST API with authentication and rate limiting",
            'data_science': "Analyze customer churn data and build a predictive model",
            'writing': "Write a technical blog post about microservices architecture"
        }
        
        task_description = tasks.get(domain, "Complete a general task")
        
        # 分析任务
        print(f"\nTask: {task_description}")
        print(f"\nAnalyzing...")
        
        result = agent.analyze_task(task_description)
        
        print(f"\n✓ Analysis completed")
        print(f"  Domain: {result['domain']}")
        print(f"  Expertise: {result['expertise_level']}")
        
        # 这里可以根据需要处理分析结果
        # print(f"\n  Analysis: {result['analysis']}")


def demo_quick_agents():
    """快速演示多个智能体"""
    print("\n" + "=" * 60)
    print("Quick Demo: Multiple Specialized Agents")
    print("=" * 60)
    
    # 编程助手
    code_agent = SpecializedAgent(domain="programming", expertise_level="expert")
    print("\n✓ Created Programming Expert Agent")
    
    # 数据科学助手
    ds_agent = SpecializedAgent(domain="data_science", expertise_level="expert")
    print("✓ Created Data Science Expert Agent")
    
    # 写作助手
    writing_agent = SpecializedAgent(domain="writing", expertise_level="intermediate")
    print("✓ Created Writing Assistant Agent")
    
    print("\n✓ All agents ready for tasks!")


if __name__ == "__main__":
    # 注意：这个示例需要配置 API key
    # 如果没有配置，将只演示创建过程
    try:
        main()
    except Exception as e:
        print(f"\n⚠ Note: Full example requires API key configuration")
        print(f"Error: {e}")
        print("\nRunning quick demo instead...")
        demo_quick_agents()
