FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.4.29
COPY pyproject.toml .
RUN uv pip install --system --no-cache -e .
COPY sentinex_proxy/ ./sentinex_proxy/
EXPOSE 8080
CMD ["mitmdump", "-p", "8080", "-s", "sentinex_proxy/main.py", "--set", "ssl_insecure=true"]
