#!/usr/bin/env bash
# 多包一次性可编辑安装。--no-deps：依赖已声明在各自 pyproject 里，
# 但 a2n-* 尚未发布到 PyPI，全部本地可编辑安装即可。
set -e
cd "$(dirname "$0")/.."
for d in packages/*/; do
  echo "  -> $d"
  python -m pip install -e "$d" --no-deps -q
done
echo "全部安装完成"
