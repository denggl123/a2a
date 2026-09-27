#!/bin/bash
# 本机节点的看护循环 —— 计划任务侧的最小"systemd"。
#
# 为什么不靠计划任务自己的"失败时重启"（RestartOnFailure）：
# 2026-09-27 实测，节点进程被杀后任务结果仍报 0（cmd 吞了退出码），
# 失败重启根本不触发。所以重启语义必须握在自己手里：
# 节点退出 → 等 5 秒 → 重新拉起，本循环永不退出。
#
# 手动单次前台跑仍然用：bash scripts/serve_local_node.sh
# 计划任务入口：serve_local_node.cmd loop
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
n=0
while true; do
  bash "$HERE/serve_local_node.sh"
  code=$?
  n=$((n+1))
  echo "[supervisor] 节点第 $n 次退出（code=$code），5 秒后重新拉起" >&2
  sleep 5
done
