#!/usr/bin/env bash
# 入库校验唯一执行正本：本地与 CI 一律经由本脚本调用 lint（勿在别处另写命令）。
# 用法：bash scripts/lint.sh   （退出码透传自 bank.py lint）
set -u
cd "$(dirname "$0")/.." || exit 1

# 首选路径：uv（项目标准环境管理）
if command -v uv >/dev/null 2>&1; then
    uv run python scripts/checks/run_checks.py --translations-only --sample 100 || exit $?
    exec uv run python scripts/bank.py lint "$@"
fi

# 降级路径：无 uv 时用 python3，缺 pyyaml 则 pip --user 安装
if ! command -v python3 >/dev/null 2>&1; then
    echo "lint.sh: 未找到 uv 也未找到 python3，无法执行校验。" >&2
    exit 127
fi

python3 scripts/checks/run_checks.py --translations-only --sample 100 || exit $?

if ! python3 -c 'import yaml' >/dev/null 2>&1; then
    echo "lint.sh: 未检测到 pyyaml，尝试 python3 -m pip install --user pyyaml ..." >&2
    if ! python3 -m pip install --user pyyaml >&2; then
        echo "lint.sh: pyyaml 安装失败（建议安装 uv 后重试）。" >&2
        exit 1
    fi
fi

exec python3 scripts/bank.py lint "$@"
