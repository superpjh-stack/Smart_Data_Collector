FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN pip install .


FROM python:3.12-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COLLECTOR_SQLITE_PATH=/data/collector_buffer.db

COPY --from=builder /opt/venv /opt/venv

RUN useradd --create-home --uid 10001 collector \
    && mkdir -p /data \
    && chown collector:collector /data

VOLUME ["/data"]
USER collector
WORKDIR /home/collector

CMD ["python", "-m", "collector.main"]
