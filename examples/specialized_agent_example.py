"""
专业智能体示例 - 演示如何使用针对特定领域优化的智能体
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.agents import SpecializedAgent, create_custom_model
from src.utils.config_loader import load_config

# 加载配置
config = load_config()
print(f"✓ 使用模型: {config['llm']['model_name']}")
print(f"✓ API Base: {config['llm']['base_url']}")


def main():
    """主函数"""
    print("=" * 60)
    print("CAMEL-AI Specialized Agent Example")
    print("=" * 60)
    
    # 创建模型（所有智能体共用）
    model = create_custom_model(
        model_name=config['llm']['model_name'],
        api_key=config['llm']['api_key'],
        base_url=config['llm']['base_url'],
        temperature=config['llm']['temperature']
    )
    
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
            model=model
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
    
    config = load_config()
    
    # 创建共享模型
    model = create_custom_model(
        model_name=config['llm']['model_name'],
        api_key=config['llm']['api_key'],
        base_url=config['llm']['base_url'],
        temperature=config['llm']['temperature']
    )
    
    # 编程助手
    code_agent = SpecializedAgent(
        domain="programming", 
        expertise_level="expert",
        model=model
    )
    print(f"\n✓ Created Programming Expert Agent ({config['llm']['model_name']})")
    
    # 数据科学助手
    ds_agent = SpecializedAgent(
        domain="data_science", 
        expertise_level="expert",
        model=model
    )
    print(f"✓ Created Data Science Expert Agent ({config['llm']['model_name']})")
    
    # 写作助手
    writing_agent = SpecializedAgent(
        domain="writing", 
        expertise_level="intermediate",
        model=model
    )
    print(f"✓ Created Writing Assistant Agent ({config['llm']['model_name']})")
    
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
