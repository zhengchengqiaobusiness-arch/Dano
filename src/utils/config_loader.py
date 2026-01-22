"""
配置加载工具 - 支持从 YAML 文件加载模型配置
"""

import os
import yaml
from typing import Dict, Any, Optional
from pathlib import Path


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    加载配置文件
    
    Args:
        config_path: 配置文件路径，默认为项目根目录的 config.yaml
        
    Returns:
        配置字典
    """
    if config_path is None:
        # 默认使用项目根目录的 config.yaml
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / "config.yaml"
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    return config


def get_model_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    获取模型配置
    
    Args:
        config: 配置字典，如果为 None 则自动加载
        
    Returns:
        模型配置字典，包含 API key、base URL 等
    """
    if config is None:
        config = load_config()
    
    llm_config = config.get('llm', {})
    
    # 构建模型配置
    model_config = {
        'api_key': llm_config.get('api_key'),
        'base_url': llm_config.get('base_url'),
        'model': llm_config.get('model_name'),
        'temperature': llm_config.get('temperature', 0.0),
    }
    
    return model_config


def setup_custom_model_from_config(config_path: Optional[str] = None):
    """
    从配置文件设置自定义模型（SiliconFlow 等）
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        配置好的模型配置字典
    """
    config = load_config(config_path)
    model_config = get_model_config(config)
    
    # 设置环境变量（如果需要）
    if model_config.get('api_key'):
        os.environ['OPENAI_API_KEY'] = model_config['api_key']
    if model_config.get('base_url'):
        os.environ['OPENAI_API_BASE'] = model_config['base_url']
    
    return model_config


def create_model_from_config(config_path: Optional[str] = None):
    """
    从配置创建模型实例
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        模型实例
    """
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    
    config = load_config(config_path)
    llm_config = config.get('llm', {})
    
    # 创建模型配置
    model_config_dict = {
        'model': llm_config.get('model_name', 'deepseek-ai/DeepSeek-V3.2'),
        'api_key': llm_config.get('api_key'),
        'url': llm_config.get('base_url', 'https://api.siliconflow.cn/v1'),
        'temperature': llm_config.get('temperature', 0.0),
    }
    
    # 使用 OpenAI 兼容的接口
    model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=llm_config.get('model_name', 'deepseek-ai/DeepSeek-V3.2'),
        model_config_dict=model_config_dict
    )
    
    return model
