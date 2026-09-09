FROM python:3.13-slim

WORKDIR /app
# SDK 零第三方依赖，直接拷源码即可，不用装任何包
COPY packages/a2n-sdk/src/a2n_sdk/ /app/a2n_sdk/
COPY docker/agent_node.py /app/agent_node.py

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV A2N_PLATFORM=http://host.docker.internal:8000

CMD ["python", "-u", "/app/agent_node.py"]
