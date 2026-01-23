"""
太平洋科技新闻查询 - 完整超时修复
修复所有超时点：
1. BaseBrowser._wait_for_load: 20秒 → 60秒
2. BrowserToolkit.timeout: 180秒 → 600秒  
3. Playwright page.goto: 30秒 → 90秒
"""

import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from camel.toolkits import BrowserToolkit
from camel.toolkits.browser_toolkit import BaseBrowser
from src.agents import create_custom_model
from src.utils.config_loader import load_config


def patch_all_browser_timeouts():
    """完整的超时补丁"""
    
    # Patch 1: 增加 _wait_for_load 超时
    original_wait_for_load = BaseBrowser._wait_for_load
    
    def patched_wait_for_load(self, timeout: int = 60):
        return original_wait_for_load(self, timeout)
    
    BaseBrowser._wait_for_load = patched_wait_for_load
    print("✅ Patch 1: _wait_for_load 超时 20秒 → 60秒")
    
    # Patch 2: 增加 visit_page 中的 page.goto 超时
    original_visit_page = BaseBrowser.visit_page
    
    def patched_visit_page(self, url: str):
        """修改后的 visit_page，增加 goto 超时"""
        assert self.page is not None
        # 关键：设置 timeout 为 90秒（90000毫秒）
        self.page.goto(url, timeout=90000)  
        self._wait_for_load()
        self.page_url = url
    
    BaseBrowser.visit_page = patched_visit_page
    print("✅ Patch 2: page.goto 超时 30秒 → 90秒")


def main():
    print("=" * 80)
    print("🔍 太平洋科技网站热门新闻查询 - 完整修复版")
    print("=" * 80)
    
    print("\n🔧 应用完整超时补丁...")
    patch_all_browser_timeouts()
    
    config = load_config()
    
    # 创建模型
    print("\n📦 创建模型...")
    planning_model = create_custom_model(
        model_name=config['llm']['model_name'],
        api_key=config['llm']['api_key'],
        base_url=config['llm']['base_url'],
        temperature=0.0
    )
    
    web_model = create_custom_model(
        model_name="Qwen/Qwen3-VL-235B-A22B-Thinking",
        api_key=config['llm']['api_key'],
        base_url=config['llm']['base_url'],
        temperature=0.0
    )
    
    print("✓ 模型创建成功")
    
    # 初始化 BrowserToolkit
    print("\n🌐 初始化 BrowserToolkit...")
    browser_toolkit = BrowserToolkit(
        planning_agent_model=planning_model,
        web_agent_model=web_model
    )
    
    # 设置总超时为 600 秒
    browser_toolkit.timeout = 600.0
    
    print(f"✓ BrowserToolkit 初始化成功")
    print(f"  - browse_url 总超时: {browser_toolkit.timeout}秒")
    print(f"  - page.goto 超时: 90秒")
    print(f"  - _wait_for_load 超时: 60秒")
    
    url = "https://www.pconline.com.cn/"
    
    # 任务：提取新闻（带分类）
    task_prompt = """
Browse the Chinese tech website homepage (PConline/太平洋科技).

Task:
1. Find 5-7 hot news headlines
2. Note category for each if visible (手机/AI/笔记本/相机/游戏)

Format:
**1. [Category] Headline**
**2. [Category] Headline**
...

Be concise and direct.
"""
    
    print("\n" + "=" * 80)
    print("📋 任务详情")
    print("=" * 80)
    print(f"网站: {url}")
    print(f"任务: 提取5-7条热门新闻（带分类）")
    print(f"总超时: {browser_toolkit.timeout}秒")
    
    print("\n⏳ 开始执行...")
    print("-" * 80)
    start_time = time.time()
    
    try:
        result = browser_toolkit.browse_url(
            task_prompt=task_prompt,
            start_url=url,
        )
        
        elapsed = time.time() - start_time
        
        print(f"\n✅ 成功完成! 耗时: {elapsed:.1f}秒")
        print("\n" + "=" * 80)
        print("📊 提取结果")
        print("=" * 80)
        print()
        print(result)
        print()
        print("=" * 80)
        
        # 保存结果
        output_file = "../pconline_news_success.txt"
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("太平洋科技网站热门新闻 - 成功提取\n")
            f.write(f"查询时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"耗时: {elapsed:.1f}秒 / {browser_toolkit.timeout}秒\n")
            f.write("=" * 80 + "\n\n")
            f.write(result)
            f.write("\n\n" + "=" * 80 + "\n")
        
        print(f"\n✅ 结果已保存: {output_file}")
        print(f"🎉 任务完成！耗时 {elapsed:.1f}/{browser_toolkit.timeout}秒")
        
        # 成功总结
        print("\n" + "=" * 80)
        print("📊 超时优化总结")
        print("=" * 80)
        print("优化措施:")
        print("  1. _wait_for_load: 20秒 → 60秒")
        print("  2. page.goto: 30秒 → 90秒")
        print("  3. browse_url 总超时: 180秒 → 600秒")
        print(f"\n实际耗时: {elapsed:.1f}秒")
        print("状态: ✅ 成功")
        
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n❌ 执行失败! 耗时: {elapsed:.1f}秒")
        print("=" * 80)
        print(f"错误: {e}")
        print("=" * 80)
        
        error_str = str(e).lower()
        if "timeout" in error_str or "timed out" in error_str:
            print(f"\n🔍 超时分析:")
            print(f"  - 设置超时: {browser_toolkit.timeout}秒")
            print(f"  - 实际耗时: {elapsed:.1f}秒")
            
            if "30000ms" in str(e) or "30 seconds" in str(e):
                print(f"  - 问题: page.goto 仍然超时（可能 patch 未生效）")
            
            print(f"\n💡 建议:")
            print("  1. 进一步简化任务（减少到3条新闻）")
            print("  2. 使用更快的视觉模型")
            print("  3. 检查网络连接")
        else:
            print("\n详细错误:")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
