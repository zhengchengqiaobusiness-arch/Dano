"""
专业领域智能体 - 针对特定任务的智能体
"""

from typing import Optional, Any, Dict
from camel.agents import ChatAgent
from camel.messages import BaseMessage
from camel.types import ModelType, RoleType


class SpecializedAgent(ChatAgent):
    """
    专业领域智能体，针对特定领域进行优化
    
    示例：代码分析智能体
    """
    
    def __init__(
        self,
        domain: str = "general",
        expertise_level: str = "expert",
        model: Optional[Any] = None,
        **kwargs
    ):
        """
        初始化专业智能体
        
        Args:
            domain: 专业领域（如 'programming', 'data_science', 'writing'）
            expertise_level: 专业程度（'beginner', 'intermediate', 'expert'）
            model: 模型（可以是 ModelType, str, 或模型实例）
        """
        # 根据领域和专业程度构建系统消息
        system_message_content = self._build_system_message(domain, expertise_level)
        
        super().__init__(
            system_message=system_message_content,
            model=model,
            **kwargs
        )
        
        self.domain = domain
        self.expertise_level = expertise_level
    
    def _build_system_message(self, domain: str, expertise_level: str) -> str:
        """构建系统消息"""
        domain_prompts = {
            'programming': f"""You are an {expertise_level} programming assistant. 
You specialize in writing clean, efficient, and well-documented code. 
You follow best practices and can explain complex concepts clearly.""",
            
            'data_science': f"""You are an {expertise_level} data science consultant.
You excel at data analysis, statistical modeling, and machine learning.
You provide actionable insights backed by data.""",
            
            'writing': f"""You are an {expertise_level} writing assistant.
You help with creative writing, technical documentation, and content creation.
You maintain clarity, coherence, and engaging style.""",
            
            'general': f"""You are an {expertise_level} AI assistant.
You provide helpful, accurate, and well-reasoned responses."""
        }
        
        return domain_prompts.get(domain, domain_prompts['general'])
    
    def analyze_task(self, task_description: str) -> Dict[str, Any]:
        """
        分析任务并提供建议
        
        Args:
            task_description: 任务描述
            
        Returns:
            任务分析结果
        """
        analysis_message = BaseMessage.make_user_message(
            role_name="User",
            content=f"""Please analyze this task:
{task_description}

Provide:
1. Task complexity assessment
2. Key steps needed
3. Potential challenges
4. Recommended approach"""
        )
        
        response = self.step(analysis_message)
        
        return {
            'domain': self.domain,
            'expertise_level': self.expertise_level,
            'analysis': response
        }
