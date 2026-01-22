#!/bin/bash

# Dano CAMEL-AI Project - Conda Setup Script

echo "=========================================="
echo "Dano CAMEL-AI Project - Conda Setup"
echo "=========================================="

# 检查 conda 是否安装
if ! command -v conda &> /dev/null
then
    echo "❌ Error: conda not found"
    echo "Please install Anaconda or Miniconda first:"
    echo "  https://docs.conda.io/en/latest/miniconda.html"
    exit 1
fi

echo ""
echo "✓ Found conda: $(conda --version)"

# 获取项目根目录
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$PROJECT_DIR"

echo "✓ Project directory: $PROJECT_DIR"

# 删除旧环境（如果存在）
echo ""
read -p "Remove existing 'dano-camel-ai' environment if exists? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
    echo "Removing existing environment..."
    conda env remove -n dano-camel-ai -y 2>/dev/null || true
fi

# 从 environment.yml 创建环境
echo ""
echo "Creating conda environment from environment.yml..."
conda env create -f environment.yml

if [ $? -ne 0 ]; then
    echo "❌ Error: Failed to create conda environment"
    exit 1
fi

echo ""
echo "✓ Conda environment created successfully!"

# 激活环境并安装项目
echo ""
echo "Activating environment and installing project..."
eval "$(conda shell.bash hook)"
conda activate dano-camel-ai

# 安装项目（可编辑模式）
pip install -e .

echo ""
echo "✓ Project installed in editable mode"

# 创建 .env 文件（如果不存在）
if [ ! -f .env ]; then
    echo ""
    echo "Creating .env file from template..."
    cat > .env << 'EOF'
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
echo "=========================================="
echo "Setup Complete! 🎉"
echo "=========================================="
echo ""
echo "Python version: $(python --version)"
echo "Conda environment: dano-camel-ai"
echo ""
echo "Next steps:"
echo "1. Activate the environment:"
echo "   conda activate dano-camel-ai"
echo ""
echo "2. Edit .env file with your API keys:"
echo "   nano .env"
echo ""
echo "3. Run examples:"
echo "   python examples/basic_agent_example.py"
echo ""
echo "4. Run tests:"
echo "   pytest tests/"
echo ""
echo "5. Deactivate when done:"
echo "   conda deactivate"
echo ""
