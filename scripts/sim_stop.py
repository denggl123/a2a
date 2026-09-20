"""停掉演示环境占用的端口（8000 平台 / 9102-9105 四个节点）与三个案例容器，幂等。

为什么不用现成工具：本机 `tasklist /FI` 在 Git Bash 里不可用、`wmic` 被安全策略禁用，
所以退一步用 netstat 找"谁在监听演示端口"，再按 PID 发 SIGTERM（不行再 SIGKILL）。

案例容器**一个主机端口都不占**（全靠出站连接 + 反向隧道），netstat 看不见它们，
所以得单独 `docker rm -f` —— 不然下一轮 sim_start 会撞上同名容器。

用法：python scripts/sim_stop.py
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time

PORTS = (8000, 9102, 9103, 9104, 9105)

# 与 sim_start.sh 里的 start_market_container 一一对应
CONTAINERS = ("a2n-market-video", "a2n-market-finance", "a2n-market-play")


def _docker() -> str | None:
    """找 docker CLI。shim 污染的 PATH 里常常没有，但 Docker Desktop 就在默认位置。"""
    found = shutil.which("docker")
    if found:
        return found
    fallback = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    return fallback if os.path.exists(fallback) else None


def stop_containers() -> None:
    exe = _docker()
    if not exe:
        print("[sim] 没找到 docker CLI，跳过案例容器清理", file=sys.stderr)
        return
    for name in CONTAINERS:
        try:
            p = subprocess.run([exe, "rm", "-f", name], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            print(f"[sim] 清容器 {name} 失败：{e}", file=sys.stderr)
            continue
        if p.returncode == 0:
            print(f"[sim] 停掉案例容器 {name}")
            continue
        # 容器不存在是常态（第一次跑），不算错；其它原因如实报出来
        err = (p.stderr or b"").decode("utf-8", "replace")
        if "No such container" not in err:
            print(f"[sim] 清容器 {name} 返回 {p.returncode}：{err.strip()[:160]}", file=sys.stderr)


def listeners() -> dict[int, int]:
    """{pid: port}：当前监听演示端口的进程。"""
    try:
        # netstat 在中文 Windows 上吐的是 GBK，硬按 UTF-8 解会直接抛 UnicodeDecodeError；
        # 我们只要数字行，所以按字节收、宽松解码。
        proc = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True)
    except OSError as e:
        print(f"[sim] 拿不到 netstat：{e}", file=sys.stderr)
        return {}
    out = (proc.stdout or b"").decode("utf-8", "replace")
    found: dict[int, int] = {}
    for line in out.splitlines():
        m = re.search(r"[:.](\d{2,5})\s+\S+\s+LISTENING\s+(\d+)", line)
        if m and int(m.group(1)) in PORTS:
            found[int(m.group(2))] = int(m.group(1))
    return found


def main() -> int:
    stop_containers()
    pids = listeners()
    if not pids:
        print("[sim] 演示端口没人占着，无需清理")
        return 0
    for pid, port in pids.items():
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"[sim] 停掉 pid={pid}（端口 {port}）")
        except OSError as e:
            print(f"[sim] pid={pid} 停不掉：{e}", file=sys.stderr)
    time.sleep(1.5)
    for pid, port in listeners().items():
        try:
            os.kill(pid, signal.SIGKILL)
            print(f"[sim] 强杀 pid={pid}（端口 {port}）")
        except OSError:
            pass
    time.sleep(0.8)
    left = listeners()
    if left:
        print(f"[sim] 还有端口没释放：{left}", file=sys.stderr)
        return 1
    print("[sim] 演示端口已清空")
    return 0


if __name__ == "__main__":
    sys.exit(main())
