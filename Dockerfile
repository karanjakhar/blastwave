FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY blastwave ./blastwave
COPY web ./web
RUN pip install --no-cache-dir .
EXPOSE 8080
CMD ["sh", "-c", "python -m blastwave.seed && uvicorn blastwave.server:app --host 0.0.0.0 --port 8080"]
