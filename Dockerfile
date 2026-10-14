FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid agent --create-home agent

# .dockerignore allows only application sources and the bundled sample data.
COPY --chown=agent:agent . .
RUN mkdir -p /app/rag/qdrant_data \
    && chown agent:agent /app/rag/qdrant_data

USER agent

CMD ["python", "main.py"]
