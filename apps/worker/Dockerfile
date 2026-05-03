FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.4.29
COPY pyproject.toml .
RUN uv pip install --system --no-cache -e ".[dev]" 2>/dev/null || uv pip install --system --no-cache -e .
COPY sentinex_worker/ ./sentinex_worker/
# Docker socket is mounted at runtime via -v /var/run/docker.sock:/var/run/docker.sock
CMD ["arq", "sentinex_worker.main.WorkerSettings"]
