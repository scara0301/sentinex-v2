FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.4.29
COPY pyproject.toml .
RUN uv pip install --system --no-cache -e ".[dev]" 2>/dev/null || uv pip install --system --no-cache -e .
COPY sentinex_api/ ./sentinex_api/
EXPOSE 8000
CMD ["uvicorn", "sentinex_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
