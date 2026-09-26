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
#
# 2026-09-25（用户拍板「容器真跑 a2n-node = 同一份软件」）再补 a2n-node 的依赖闭包：
#   a2n_node 就是那份**产品本体**（控制台 + P2P 发现 + 开关）。容器要能"像一台真机器
#   一样被 P2P 直邻发现"，就得跑它，而不是只跑一个只会注册平台的供给脚本。
#   闭包（见各自 pyproject）：a2n-node → registry / notary / acceptance / sdk
#                              registry → transport / p2p；acceptance → custodian
#   全部只依赖 kernel/store/p2p/custodian，标准库为主 —— 不新增第三方依赖。
#   不拷 a2n-node 的花，容器里 import a2n_node.p2p_service 会直接 ModuleNotFoundError。
COPY packages/a2n-kernel/src/a2n_kernel/ /app/packages/a2n-kernel/src/a2n_kernel/
COPY packages/a2n-store/src/a2n_store/ /app/packages/a2n-store/src/a2n_store/
COPY packages/a2n-p2p/src/a2n_p2p/ /app/packages/a2n-p2p/src/a2n_p2p/
COPY packages/a2n-custodian/src/a2n_custodian/ /app/packages/a2n-custodian/src/a2n_custodian/
COPY packages/a2n-transport/src/a2n_transport/ /app/packages/a2n-transport/src/a2n_transport/
COPY packages/a2n-registry/src/a2n_registry/ /app/packages/a2n-registry/src/a2n_registry/
COPY packages/a2n-notary/src/a2n_notary/ /app/packages/a2n-notary/src/a2n_notary/
COPY packages/a2n-acceptance/src/a2n_acceptance/ /app/packages/a2n-acceptance/src/a2n_acceptance/
COPY packages/a2n-sdk/src/a2n_sdk/ /app/packages/a2n-sdk/src/a2n_sdk/
COPY packages/a2n-node/src/a2n_node/ /app/packages/a2n-node/src/a2n_node/
COPY scripts/run_market_demo_agents.py scripts/run_free_demo_agents.py /app/scripts/
COPY docker/agent_node.py docker/market_node.py docker/node_entry.py /app/docker/

# 只有 a2n_p2p 要第三方依赖（ed25519），其余全是标准库。
# SDK 本身仍然零依赖 —— 这条不许因为容器方便就破掉。
RUN pip install --no-cache-dir cryptography

ENV PYTHONPATH=/app/packages/a2n-kernel/src:/app/packages/a2n-store/src:/app/packages/a2n-p2p/src:/app/packages/a2n-custodian/src:/app/packages/a2n-transport/src:/app/packages/a2n-registry/src:/app/packages/a2n-notary/src:/app/packages/a2n-acceptance/src:/app/packages/a2n-sdk/src:/app/packages/a2n-node/src
ENV PYTHONUNBUFFERED=1
ENV A2N_PLATFORM=http://host.docker.internal:18787
ENV A2N_SCRIPTS_DIR=/app/scripts
# 钥匙落盘目录：挂卷进去，重启才复用同一条上架（uid 由钥匙派生）
ENV A2N_STATE_DIR=/app/state

# 默认仍是那个单技能 OCR 节点；行业案例走
#   -e A2N_CONTAINER=video-studio python -u /app/docker/market_node.py
CMD ["python", "-u", "/app/docker/agent_node.py"]
