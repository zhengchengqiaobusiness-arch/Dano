"""
自定义 ChatAgent - 继承 CAMEL-AI 基类进行扩展
"""

import os
from typing import Optional, Any, Dict
from camel.agents import ChatAgent
from camel.messages import BaseMessage
from camel.types import ModelType, RoleType, ModelPlatformType
from camel.models import ModelFactory


def create_custom_model(
    model_name: str,
    api_key: str,
    base_url: str,
    temperature: float = 0.0
):
    """
    创建自定义模型（用于 SiliconFlow 等）
    
    Args:
        model_name: 模型名称，如 'deepseek-ai/DeepSeek-V3.2'
        api_key: API密钥
        base_url: API基础URL
        temperature: 温度参数
    
    Returns:
        模型实例
    """
    return ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=model_name,
        api_key=api_key,
        url=base_url,
        model_config_dict={'temperature': temperature}
    )


class CustomChatAgent(ChatAgent):
    """
    自定义聊天智能体，继承 CAMEL-AI 的 ChatAgent
    
    扩展功能：
    - 添加消息历史记录
    - 添加自定义提示词前缀
    - 添加响应后处理
    """
    
    def __init__(
        self,
        system_message: Optional[BaseMessage] = None,
        model: Optional[Any] = None,
        custom_prefix: str = "",
        **kwargs
    ):
        """
        初始化自定义 ChatAgent
        
        Args:
            system_message: 系统消息
            model: 模型实例（使用 create_custom_model() 创建）
            custom_prefix: 自定义提示词前缀
            **kwargs: 其他参数传递给父类
        """
        super().__init__(
            system_message=system_message,
            model=model,
            **kwargs
        )
        self.custom_prefix = custom_prefix
        self.message_history = []
        self.response_count = 0
    
    def step(self, input_message: BaseMessage) -> Any:
        """
        重写 step 方法，添加自定义逻辑
        
        Args:
            input_message: 输入消息
            
        Returns:
            智能体响应
        """
        # 前处理：记录输入消息
        self.message_history.append({
            'type': 'input',
            'message': input_message,
            'timestamp': self._get_timestamp()
        })
        
        # 如果有自定义前缀，添加到消息中
        if self.custom_prefix:
            modified_content = f"{self.custom_prefix}\n\n{input_message.content}"
            input_message = BaseMessage.make_user_message(
                role_name=input_message.role_name,
                content=modified_content
            )
        
        # 调用父类的 step 方法
        response = super().step(input_message)
        
        # 后处理：记录响应
        self.response_count += 1
        self.message_history.append({
            'type': 'response',
            'response': response,
            'timestamp': self._get_timestamp(),
            'count': self.response_count
        })
        
        return response
    
    def get_message_history(self) -> list:
        """获取消息历史记录"""
        return self.message_history
    
    def clear_history(self):
        """清空消息历史"""
        self.message_history = []
        self.response_count = 0
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'total_messages': len(self.message_history),
            'response_count': self.response_count,
            'custom_prefix': self.custom_prefix,
        }
    
    @staticmethod
    def _get_timestamp() -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()
