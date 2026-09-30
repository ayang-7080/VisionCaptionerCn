#!/bin/bash

# 中文界面启动器: 通过 launcher.py 外挂汉化, 不修改项目源码
# 环境统一基于 uv (https://docs.astral.sh/uv/), 首次运行自动创建 venv 并安装依赖
# 想用回英文原版, 运行 bash run.sh 即可

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

echo "------------------------------------------"
echo "  STARTING VISION CAPTIONER (中文界面)"
echo "------------------------------------------"

# 1. 检查 uv
if ! command -v uv &> /dev/null; then
    echo "[错误] 未找到 uv, 请先安装:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# 2. venv 不存在则初始化 (只需一次, 与说明.txt 的 Windows 流程一致)
if [ ! -f "venv/bin/activate" ]; then
    echo "首次运行: 创建 venv 并安装依赖 (含 torch cu126, 下载量较大请耐心等待)..."
    uv venv venv --seed --python 3.12
    # CUDA 12.6 版 torch, 需要驱动 >= 560
    uv pip install --python venv/bin/python torch torchvision \
        --index-url https://download.pytorch.org/whl/cu126
    uv pip install --python venv/bin/python -r requirements.txt
fi

# 3. 激活环境并启动 (首次使用前可先验证汉化钩子: python launcher.py --selftest)
source venv/bin/activate
python launcher.py
