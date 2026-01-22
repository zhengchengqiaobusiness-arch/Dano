"""
测试自定义智能体
"""

import os
from src.agents import CustomChatAgent


class TestCustomChatAgent:
    """CustomChatAgent 测试类"""
    
    def setup_method(self):
        """每个测试前设置 API key"""
        if not os.environ.get('OPENAI_API_KEY'):
            os.environ['OPENAI_API_KEY'] = 'sk-test-key-placeholder'
    
    def test_initialization_with_prefix(self):
        """测试带前缀初始化"""
        agent = CustomChatAgent(custom_prefix="[测试]")
        
        assert agent.custom_prefix == "[测试]"
        assert isinstance(agent.message_history, list)
        assert len(agent.message_history) == 0
        assert agent.response_count == 0
    
    def test_initialization_without_prefix(self):
        """测试不带前缀初始化"""
        agent = CustomChatAgent()
        
        assert agent.custom_prefix == ""
        assert agent.response_count == 0
    
    def test_get_statistics(self):
        """测试获取统计信息"""
        agent = CustomChatAgent(custom_prefix="[统计测试]")
        
        stats = agent.get_statistics()
        
        assert isinstance(stats, dict)
        assert 'total_messages' in stats
        assert 'response_count' in stats
        assert 'custom_prefix' in stats
        assert stats['total_messages'] == 0
        assert stats['response_count'] == 0
        assert stats['custom_prefix'] == "[统计测试]"
    
    def test_clear_history(self):
        """测试清空历史记录"""
        agent = CustomChatAgent()
        
        # 手动添加一些历史记录
        agent.message_history.append({'test': 'data'})
        agent.response_count = 5
        
        assert len(agent.message_history) > 0
        assert agent.response_count == 5
        
        # 清空
        agent.clear_history()
        
        assert len(agent.message_history) == 0
        assert agent.response_count == 0
    
    def test_get_message_history(self):
        """测试获取消息历史"""
        agent = CustomChatAgent()
        
        history = agent.get_message_history()
        
        assert isinstance(history, list)
        assert len(history) == 0
