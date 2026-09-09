@echo off
REM 多包一次性可编辑安装（Windows）
for /d %%d in (packages\*) do (
  echo   -^> %%d
  python -m pip install -e "%%d" --no-deps -q
)
echo done
