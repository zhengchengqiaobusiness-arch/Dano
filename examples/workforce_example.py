"""
Workforce 示例 - 演示如何使用自定义 Workforce 管理多个任务
"""

import os
from dotenv import load_dotenv
from camel.tasks import Task

from src.workforce import CustomWorkforce

# 加载环境变量
load_dotenv()


def main():
    """主函数"""
    print("=" * 60)
    print("CAMEL-AI Custom Workforce Example")
    print("=" * 60)
    
    # 创建自定义 Workforce
    workforce = CustomWorkforce(
        description="Development Team Workforce",
        enable_priority=True,
        enable_monitoring=True
    )
    
    print("\n✓ Created CustomWorkforce with monitoring enabled")
    
    # 定义任务列表
    tasks = [
        {
            'description': "Design database schema for user management system",
            'priority': 5
        },
        {
            'description': "Implement REST API endpoints for CRUD operations",
            'priority': 4
        },
        {
            'description': "Write unit tests for authentication module",
            'priority': 3
        },
        {
            'description': "Create API documentation with examples",
            'priority': 2
        },
        {
            'description': "Set up CI/CD pipeline for automated deployment",
            'priority': 1
        }
    ]
    
    print(f"\n📋 Processing {len(tasks)} tasks...")
    
    # 处理每个任务
    results = []
    for task_info in tasks:
        # 创建任务对象
        task = Task(
            content=task_info['description'],
            id=f"task_{len(results) + 1}"
        )
        
        # 使用 Workforce 处理任务
        result = workforce.custom_process_task(
            task=task,
            priority=task_info['priority'],
            max_retries=3
        )
        
        results.append(result)
    
    # 显示结果摘要
    print(f"\n{'=' * 60}")
    print("Task Results Summary")
    print(f"{'=' * 60}")
    
    for i, result in enumerate(results, 1):
        status = "✓" if result['success'] else "✗"
        task_desc = tasks[i-1]['description']
        exec_time = result['execution_time']
        
        print(f"\n{status} Task {i}:")
        print(f"  Description: {task_desc}")
        print(f"  Execution Time: {exec_time:.2f}s")
        print(f"  Retries: {result['retry_count']}")
        
        if not result['success']:
            print(f"  Error: {result['error']}")
    
    # 显示 Workforce 指标
    print(f"\n{'=' * 60}")
    print("Workforce Metrics")
    print(f"{'=' * 60}")
    
    metrics = workforce.get_metrics()
    print(f"\n  Total Tasks: {metrics['total_tasks']}")
    print(f"  Successful: {metrics['successful_tasks']}")
    print(f"  Failed: {metrics['failed_tasks']}")
    print(f"  Success Rate: {metrics['success_rate'] * 100:.1f}%")
    print(f"  Total Time: {metrics['total_execution_time']:.2f}s")
    print(f"  Average Time: {metrics['average_execution_time']:.2f}s")


def demo_quick_workforce():
    """快速演示 Workforce 创建"""
    print("\n" + "=" * 60)
    print("Quick Demo: Workforce Creation")
    print("=" * 60)
    
    workforce = CustomWorkforce(
        description="Quick Demo Workforce",
        enable_priority=True,
        enable_monitoring=True
    )
    
    print("\n✓ Workforce created successfully")
    print(f"  Description: {workforce.description if hasattr(workforce, 'description') else 'N/A'}")
    print(f"  Priority Enabled: {workforce.enable_priority}")
    print(f"  Monitoring Enabled: {workforce.enable_monitoring}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n⚠ Note: Full example may require additional configuration")
        print(f"Error: {e}")
        print("\nRunning quick demo instead...")
        demo_quick_workforce()
