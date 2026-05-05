#!/usr/bin/env bash
# pddbot 一键启动 (macOS / Linux)
#   - 自动安装 uv
#   - uv venv + uv pip install (镜像源由 uv.toml 固化)
#   - uv run 装 playwright chromium 内核
#   - uv run 启动 GUI
#
# 双击或在终端执行 ./start.sh 即可。

set -e

cd "$(dirname "$0")"

echo "============================================================"
echo "  pddbot 启动器"
echo "============================================================"

# ---------- 1. 确认 uv ----------
if ! command -v uv >/dev/null 2>&1; then
    echo ">>> 未检测到 uv,自动安装..."
    if ! command -v curl >/dev/null 2>&1; then
        echo "错误:需要 curl 来下载 uv 安装脚本,请先安装 curl"
        read -n 1 -s -r -p "按任意键退出..."
        exit 1
    fi
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv 安装到 ~/.local/bin 或 ~/.cargo/bin,补到 PATH
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        echo "错误:uv 安装后仍找不到,请手动加入 PATH 后重试"
        echo "      文档 https://docs.astral.sh/uv/getting-started/installation/"
        read -n 1 -s -r -p "按任意键退出..."
        exit 1
    fi
fi
echo "    uv: $(uv --version)"

# ---------- 2. 虚拟环境 ----------
if [ ! -d ".venv" ]; then
    echo ">>> 创建虚拟环境 .venv (Python 3.11)"
    uv venv --python 3.11
else
    echo ">>> 复用已存在的 .venv"
fi

# ---------- 3. Python 依赖 ----------
echo ">>> 同步 Python 依赖 (uv pip install -r requirements.txt)"
uv pip install -r requirements.txt

# ---------- 4. Playwright Chromium 内核 ----------
# 已装过的话 playwright 自己会跳过下载,很快
echo ">>> 检查 / 安装 Playwright Chromium"
uv run python -m playwright install chromium

# ---------- 5. 启动 GUI ----------
echo ">>> 启动 GUI (uv run python -m gui.app)"
exec uv run python -m gui.app "$@"
