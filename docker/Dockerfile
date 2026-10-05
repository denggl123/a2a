FROM python:3.13-slim
WORKDIR /app
COPY packages/a2n-kernel /app/packages/a2n-kernel
COPY packages/a2n-p2p /app/packages/a2n-p2p
COPY packages/a2n-acceptance /app/packages/a2n-acceptance
COPY packages/a2n-sdk /app/packages/a2n-sdk
COPY packages/a2n-node /app/packages/a2n-node
COPY docker/node_entry.py /app/docker/node_entry.py
RUN pip install --no-cache-dir cryptography && pip install --no-cache-dir --no-deps /app/packages/*
ENV PYTHONUNBUFFERED=1 A2N_HOME=/app/state/node A2N_PORT=8771 A2N_PUBLIC_PORT=8788
CMD ["python", "-u", "/app/docker/node_entry.py"]
