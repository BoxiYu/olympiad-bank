#!/bin/bash
# macOS 双击入口：打开训练菜单（菜单本体在 scripts/menu.py，本文件只负责启动）。
# 首次被系统拦截时：右键本文件 → 打开。
cd "$(dirname "$0")" || exit 1
if ! command -v uv >/dev/null 2>&1 && [ -x "$HOME/.local/bin/uv" ]; then
  export PATH="$HOME/.local/bin:$PATH"   # uv 默认装在 ~/.local/bin，双击启动时不读 shell 配置
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "还没安装 uv（Python 环境管家，装一次即可）。"
  echo "请打开「终端」执行下面这一行，然后重新双击本文件："
  echo
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo
  read -r -p "（回车关闭）"
  exit 1
fi
uv run python scripts/menu.py
read -r -p "（回车关闭窗口）"
