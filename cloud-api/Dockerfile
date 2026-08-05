# ── Builder: resolve + install deps into a self-contained venv ──────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
COPY src ./src

RUN pip install .

# ── Runtime ────────────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "cloud_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
