"""
测试自定义 Workforce
"""

from src.workforce import CustomWorkforce
from camel.tasks import Task


class TestCustomWorkforce:
    """CustomWorkforce 测试类"""
    
    def test_initialization_default(self):
        """测试默认初始化"""
        workforce = CustomWorkforce()
        
        assert workforce.enable_priority == True
        assert workforce.enable_monitoring == True
        assert isinstance(workforce.task_metrics, list)
        assert isinstance(workforce.completed_tasks, list)
    
    def test_initialization_with_params(self):
        """测试带参数初始化"""
        workforce = CustomWorkforce(
            description="测试工作组",
            enable_priority=False,
            enable_monitoring=False
        )
        
        assert workforce.enable_priority == False
        assert workforce.enable_monitoring == False
    
    def test_empty_metrics(self):
        """测试空指标"""
        workforce = CustomWorkforce()
        
        metrics = workforce.get_metrics()
        
        assert isinstance(metrics, dict)
        assert metrics['total_tasks'] == 0
        assert metrics['successful_tasks'] == 0
        assert metrics['failed_tasks'] == 0
        assert metrics['total_execution_time'] == 0
        assert metrics['average_execution_time'] == 0
        assert metrics['success_rate'] == 0
    
    def test_task_processing(self):
        """测试任务处理"""
        workforce = CustomWorkforce()
        
        task = Task(content="测试任务", id="test-1")
        result = workforce.custom_process_task(task, priority=1, max_retries=1)
        
        assert isinstance(result, dict)
        assert 'task' in result
        assert 'result' in result
        assert 'execution_time' in result
        assert 'success' in result
        assert result['success'] == True
    
    def test_metrics_after_task(self):
        """测试任务后的指标"""
        workforce = CustomWorkforce()
        
        task1 = Task(content="任务1", id="t1")
        task2 = Task(content="任务2", id="t2")
        
        workforce.custom_process_task(task1)
        workforce.custom_process_task(task2)
        
        metrics = workforce.get_metrics()
        
        assert metrics['total_tasks'] == 2
        assert metrics['successful_tasks'] == 2
        assert metrics['failed_tasks'] == 0
        assert metrics['success_rate'] == 1.0
