# syntax=docker/dockerfile:1
FROM python:3.12.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# Copy dependency manifests before application code to preserve the dependency layer cache.
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade pip==25.3 \
    && /opt/venv/bin/python -m pip install --no-cache-dir -r requirements.txt

FROM builder AS quality

COPY requirements-dev.txt ./
RUN /opt/venv/bin/python -m pip install --no-cache-dir -r requirements-dev.txt

WORKDIR /workspace
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 sentinelai \
    && useradd --system --uid 10001 --gid sentinelai --home-dir /workspace --no-create-home sentinelai
COPY --chown=sentinelai:sentinelai app ./app
COPY --chown=sentinelai:sentinelai alembic ./alembic
COPY --chown=sentinelai:sentinelai tests ./tests
COPY --chown=sentinelai:sentinelai pyproject.toml alembic.ini requirements.txt requirements-dev.txt ./
COPY --chown=sentinelai:sentinelai quality_gate.py ./quality_gate.py
USER sentinelai

FROM python:3.12.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 sentinelai \
    && useradd --system --uid 10001 --gid sentinelai --home-dir /app --no-create-home sentinelai

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=sentinelai:sentinelai app ./app
COPY --chown=sentinelai:sentinelai alembic ./alembic
COPY --chown=sentinelai:sentinelai alembic.ini ./alembic.ini

USER sentinelai
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=3).read()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
