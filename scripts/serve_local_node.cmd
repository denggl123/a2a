@echo off
REM 本机节点的 Windows 入口 —— 给「计划任务」这类监管者用。
REM
REM 为什么需要它：Windows 没有 systemd，而计划任务只能执行一个**可执行命令**，
REM 不能表达"先加载配置再 exec"这种逻辑；逻辑都在 bash 启动器里，这里只负责把
REM 它叫起来。手动跑与计划任务跑因此走的是**同一条**路径。
REM
REM 不写死仓库路径：%~dp0 是本脚本所在目录（...\scripts\），换机器不用改。
setlocal
set "HERE=%~dp0"
set "HERE=%HERE:\=/%"
"%ProgramFiles%\Git\bin\bash.exe" -lc "exec bash '%HERE%serve_local_node.sh'"
endlocal
