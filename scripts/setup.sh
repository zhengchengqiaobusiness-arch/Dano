#!/bin/bash

# Dano CAMEL-AI Project Setup Script

echo "=================================="
echo "Dano CAMEL-AI Project Setup"
echo "=================================="

# 检查 Python 版本
echo ""
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Found Python $python_version"

# 创建虚拟环境
echo ""
echo "Creating virtual environment..."
python3 -m venv venv

# 激活虚拟环境
echo ""
echo "Activating virtual environment..."
source venv/bin/activate

# 升级 pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip

# 安装项目依赖
echo ""
echo "Installing project dependencies..."
pip install -e .

# 安装开发依赖（可选）
read -p "Install development dependencies? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
    echo "Installing development dependencies..."
    pip install -e ".[dev]"
fi

# 创建 .env 文件（如果不存在）
if [ ! -f .env ]; then
    echo ""
    echo "Creating .env file from template..."
    cat > .env << EOF
# OpenAI API Configuration
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_API_BASE_URL=https://api.openai.com/v1

# Model Configuration
DEFAULT_MODEL=gpt-4-turbo-preview

# Other API Keys (optional)
ANTHROPIC_API_KEY=your_anthropic_api_key_here
EOF
    echo "✓ Created .env file. Please edit it with your API keys."
else
    echo ""
    echo "✓ .env file already exists."
fi

echo ""
echo "=================================="
echo "Setup Complete!"
echo "=================================="
echo ""
echo "Next steps:"
echo "1. Activate the virtual environment: source venv/bin/activate"
echo "2. Edit .env file with your API keys"
echo "3. Run examples: python examples/basic_agent_example.py"
echo ""
