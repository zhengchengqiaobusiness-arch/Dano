"""
自定义 Workforce - 扩展 CAMEL-AI 的多智能体协作框架
"""

from typing import Optional, List, Dict, Any
from camel.societies.workforce import Workforce as BaseWorkforce
from camel.tasks import Task


class CustomWorkforce(BaseWorkforce):
    """
    自定义 Workforce，继承 CAMEL-AI 的 BaseWorkforce
    
    扩展功能：
    - 添加任务优先级管理
    - 添加任务执行监控
    - 添加自定义任务分解策略
    """
    
    def __init__(
        self,
        description: str = "Custom Workforce",
        enable_priority: bool = True,
        enable_monitoring: bool = True,
        **kwargs
    ):
        """
        初始化自定义 Workforce
        
        Args:
            description: Workforce 描述
            enable_priority: 是否启用优先级管理
            enable_monitoring: 是否启用监控
            **kwargs: 其他参数传递给父类
        """
        super().__init__(description=description, **kwargs)
        
        self.enable_priority = enable_priority
        self.enable_monitoring = enable_monitoring
        self.task_metrics = []
        self.completed_tasks = []
    
    def custom_process_task(
        self,
        task: Task,
        priority: int = 0,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        自定义任务处理方法
        
        Args:
            task: 要处理的任务
            priority: 任务优先级（数值越大优先级越高）
            max_retries: 最大重试次数
            
        Returns:
            任务执行结果
        """
        import time
        start_time = time.time()
        
        # 记录任务开始
        if self.enable_monitoring:
            self._log_task_start(task, priority)
        
        # 执行任务（调用父类方法）
        result = None
        retry_count = 0
        last_error = None
        
        while retry_count < max_retries:
            try:
                # 这里可以调用父类的方法或自定义执行逻辑
                result = self._execute_task(task)
                break
            except Exception as e:
                retry_count += 1
                last_error = e
                if retry_count < max_retries:
                    print(f"Task failed, retrying ({retry_count}/{max_retries})...")
        
        # 计算执行时间
        execution_time = time.time() - start_time
        
        # 记录任务完成
        if self.enable_monitoring:
            self._log_task_completion(
                task, 
                result, 
                execution_time, 
                retry_count,
                last_error
            )
        
        return {
            'task': task,
            'result': result,
            'execution_time': execution_time,
            'retry_count': retry_count,
            'success': result is not None,
            'error': str(last_error) if last_error else None
        }
    
    def _execute_task(self, task: Task) -> Any:
        """
        执行任务的核心逻辑
        
        Args:
            task: 要执行的任务
            
        Returns:
            任务结果
        """
        # 这里实现具体的任务执行逻辑
        # 可以调用 CAMEL-AI 的方法或自定义逻辑
        print(f"Executing task: {task.content if hasattr(task, 'content') else task}")
        
        # 示例：简单返回任务描述
        return {"status": "completed", "task_info": str(task)}
    
    def _log_task_start(self, task: Task, priority: int):
        """记录任务开始"""
        log_entry = {
            'event': 'task_start',
            'task': str(task),
            'priority': priority,
            'timestamp': self._get_timestamp()
        }
        self.task_metrics.append(log_entry)
        print(f"[WORKFORCE] Starting task (priority={priority}): {task}")
    
    def _log_task_completion(
        self,
        task: Task,
        result: Any,
        execution_time: float,
        retry_count: int,
        error: Optional[Exception]
    ):
        """记录任务完成"""
        log_entry = {
            'event': 'task_complete',
            'task': str(task),
            'execution_time': execution_time,
            'retry_count': retry_count,
            'success': error is None,
            'error': str(error) if error else None,
            'timestamp': self._get_timestamp()
        }
        self.task_metrics.append(log_entry)
        self.completed_tasks.append(task)
        
        status = "✓ SUCCESS" if error is None else "✗ FAILED"
        print(f"[WORKFORCE] {status} - Task completed in {execution_time:.2f}s")
    
    def get_metrics(self) -> Dict[str, Any]:
        """获取 Workforce 指标"""
        total_time = sum(
            m['execution_time'] 
            for m in self.task_metrics 
            if m['event'] == 'task_complete'
        )
        
        success_count = sum(
            1 for m in self.task_metrics 
            if m['event'] == 'task_complete' and m['success']
        )
        
        total_count = len([
            m for m in self.task_metrics 
            if m['event'] == 'task_complete'
        ])
        
        return {
            'total_tasks': total_count,
            'successful_tasks': success_count,
            'failed_tasks': total_count - success_count,
            'total_execution_time': total_time,
            'average_execution_time': total_time / total_count if total_count > 0 else 0,
            'success_rate': success_count / total_count if total_count > 0 else 0
        }
    
    @staticmethod
    def _get_timestamp() -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()
