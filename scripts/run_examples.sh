#!/bin/bash

# Run all examples script

echo "=================================="
echo "Running CAMEL-AI Examples"
echo "=================================="

# 检查是否在虚拟环境中
if [[ -z "$VIRTUAL_ENV" ]]; then
    echo "⚠️  Warning: Not in a virtual environment"
    echo "Consider running: source venv/bin/activate"
    echo ""
fi

# 检查 .env 文件
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found"
    echo "Please create .env file with your API keys"
    exit 1
fi

echo ""
echo "Running examples..."
echo ""

# 运行基础智能体示例
echo "1️⃣  Basic Agent Example"
echo "─────────────────────────────────"
python examples/basic_agent_example.py
echo ""

# 运行专业智能体示例
echo "2️⃣  Specialized Agent Example"
echo "─────────────────────────────────"
python examples/specialized_agent_example.py
echo ""

# 运行 Workforce 示例
echo "3️⃣  Workforce Example"
echo "─────────────────────────────────"
python examples/workforce_example.py
echo ""

echo "=================================="
echo "All examples completed!"
echo "=================================="
