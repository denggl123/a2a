FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends docker-cli && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY packages/a2n-node/pyproject.toml /tmp/a2n-node-dependencies.toml
RUN python -c "import subprocess,sys,tomllib; p=tomllib.load(open('/tmp/a2n-node-dependencies.toml','rb')); subprocess.run([sys.executable,'-m','pip','install','--no-cache-dir','setuptools','wheel',*[d for d in p['project']['dependencies'] if not d.startswith('a2n-')]],check=True)"
COPY packages/a2n-kernel /app/packages/a2n-kernel
COPY packages/a2n-p2p /app/packages/a2n-p2p
COPY packages/a2n-acceptance /app/packages/a2n-acceptance
COPY packages/a2n-sdk /app/packages/a2n-sdk
COPY packages/a2n-node /app/packages/a2n-node
COPY docker/node_entry.py /app/docker/node_entry.py
RUN pip install --no-cache-dir --no-build-isolation /app/packages/* && pip check
ENV PYTHONUNBUFFERED=1 A2N_HOME=/app/state/node A2N_PORT=8771 A2N_PUBLIC_PORT=8788
CMD ["python", "-u", "/app/docker/node_entry.py"]
