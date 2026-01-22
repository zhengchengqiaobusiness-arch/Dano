"""
使用自定义模型（SiliconFlow）的示例
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.agents import CustomChatAgent
from src.utils.config_loader import load_config, create_model_from_config
from camel.messages import BaseMessage


def example_with_config_file():
    """使用配置文件创建智能体"""
    print("=" * 60)
    print("使用配置文件（SiliconFlow API）")
    print("=" * 60)
    
    try:
        # 从配置文件加载模型
        config = load_config()
        print(f"\n✓ 加载配置文件成功")
        print(f"  模型: {config['llm']['model_name']}")
        print(f"  API Base: {config['llm']['base_url']}")
        
        # 方法1: 通过环境变量设置
        os.environ['OPENAI_API_KEY'] = config['llm']['api_key']
        os.environ['OPENAI_API_BASE'] = config['llm']['base_url']
        
        # 创建智能体（使用自定义模型名称）
        agent = CustomChatAgent(
            model=config['llm']['model_name'],  # 使用配置中的模型
            custom_prefix="[SiliconFlow助手]"
        )
        
        print("\n✓ 创建智能体成功")
        print(f"  前缀: [SiliconFlow助手]")
        
    except FileNotFoundError:
        print("\n⚠ 配置文件 config.yaml 不存在")
        print("请确保 config.yaml 在项目根目录")
        return
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        return


def example_with_direct_config():
    """直接传入配置创建智能体"""
    print("\n" + "=" * 60)
    print("直接传入配置")
    print("=" * 60)
    
    try:
        config = load_config()
        
        # 设置环境变量
        os.environ['OPENAI_API_KEY'] = config['llm']['api_key']
        os.environ['OPENAI_API_BASE'] = config['llm']['base_url']
        
        # 创建智能体
        agent = CustomChatAgent(
            model=config['llm']['model_name'],
            custom_prefix="[DeepSeek助手]"
        )
        
        print("\n✓ 智能体创建成功")
        
        # 测试消息（不实际调用 API，只展示配置）
        print("\n配置信息:")
        print(f"  API Key: {config['llm']['api_key'][:20]}...")
        print(f"  Base URL: {config['llm']['base_url']}")
        print(f"  Model: {config['llm']['model_name']}")
        print(f"  Temperature: {config['llm']['temperature']}")
        
    except Exception as e:
        print(f"\n❌ 错误: {e}")


def example_config_structure():
    """展示配置文件结构"""
    print("\n" + "=" * 60)
    print("配置文件结构说明")
    print("=" * 60)
    
    example_config = """
llm:
  api_key: sk-your-api-key-here
  base_url: https://api.siliconflow.cn/v1
  model_name: deepseek-ai/DeepSeek-V3.2
  temperature: 0.0
  type: qwen_openai

embedding:
  api_base: https://api.siliconflow.cn/v1
  api_key: sk-your-api-key-here
  engine: Qwen/Qwen3-Embedding-8B

rerank:
  api_key: sk-your-api-key-here
  base_url: https://api.siliconflow.cn/v1
  model_name: Qwen/Qwen3-Reranker-8B
  temperature: 0.0
"""
    
    print("\n创建 config.yaml 文件，内容如下：")
    print(example_config)
    
    print("\n支持的模型提供商:")
    print("  - SiliconFlow (https://siliconflow.cn)")
    print("  - OpenAI")
    print("  - 任何 OpenAI 兼容的 API")


def main():
    """主函数"""
    print("=" * 60)
    print("自定义模型配置示例")
    print("=" * 60)
    
    # 检查配置文件是否存在
    config_path = Path(__file__).parent.parent / "config.yaml"
    
    if config_path.exists():
        print(f"\n✓ 找到配置文件: {config_path}")
        example_with_config_file()
        example_with_direct_config()
    else:
        print(f"\n⚠ 配置文件不存在: {config_path}")
        print("\n请创建 config.yaml 文件，参考以下结构：")
        example_config_structure()
    
    print("\n" + "=" * 60)
    print("提示:")
    print("=" * 60)
    print("1. 确保 config.yaml 在项目根目录")
    print("2. 填入正确的 API key 和 base_url")
    print("3. 选择合适的模型名称")
    print("4. 可以使用任何 OpenAI 兼容的 API")


if __name__ == "__main__":
    main()
