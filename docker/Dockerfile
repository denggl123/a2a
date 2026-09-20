FROM python:3.13-slim

WORKDIR /app

# 容器里跑的是**和主机同一份**供给方程序、同一份 SDK 源码 —— 不另写一套案例。
#
# 目录**照抄仓库的形状**（packages/<pkg>/src/<mod>/），不是随便铺平到 /app 下：
# a2n_store/db.py 的默认库路径是 `Path(__file__).resolve().parents[4] / "data"`，
# 铺平之后 parents[4] 直接越界 IndexError（2026-09-19 真踩过：三个容器全部
# Exited(1)，而控制台上只看到"案例没上架"）。形状一致，相对路径的默认值才一致。
#
# 多拷四个包是为了让容器真的能自己造卡：
#   a2n_p2p       卡上的身份与署名（ed25519）
#   a2n_custodian 币种精度的**唯一事实源**（容器里也不许自己抄一张小数位表）
#   a2n_kernel / a2n_store  上面两个包的依赖
COPY packages/a2n-kernel/src/a2n_kernel/ /app/packages/a2n-kernel/src/a2n_kernel/
COPY packages/a2n-store/src/a2n_store/ /app/packages/a2n-store/src/a2n_store/
COPY packages/a2n-p2p/src/a2n_p2p/ /app/packages/a2n-p2p/src/a2n_p2p/
COPY packages/a2n-custodian/src/a2n_custodian/ /app/packages/a2n-custodian/src/a2n_custodian/
COPY packages/a2n-sdk/src/a2n_sdk/ /app/packages/a2n-sdk/src/a2n_sdk/
COPY scripts/run_market_demo_agents.py scripts/run_free_demo_agents.py /app/scripts/
COPY docker/agent_node.py docker/market_node.py /app/docker/

# 只有 a2n_p2p 要第三方依赖（ed25519），其余全是标准库。
# SDK 本身仍然零依赖 —— 这条不许因为容器方便就破掉。
RUN pip install --no-cache-dir cryptography

ENV PYTHONPATH=/app/packages/a2n-kernel/src:/app/packages/a2n-store/src:/app/packages/a2n-p2p/src:/app/packages/a2n-custodian/src:/app/packages/a2n-sdk/src
ENV PYTHONUNBUFFERED=1
ENV A2N_PLATFORM=http://host.docker.internal:8000
ENV A2N_SCRIPTS_DIR=/app/scripts
# 钥匙落盘目录：挂卷进去，重启才复用同一条上架（uid 由钥匙派生）
ENV A2N_STATE_DIR=/app/state

# 默认仍是那个单技能 OCR 节点；行业案例走
#   -e A2N_CONTAINER=video-studio python -u /app/docker/market_node.py
CMD ["python", "-u", "/app/docker/agent_node.py"]
