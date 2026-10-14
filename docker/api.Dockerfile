# ModelAtlas API image (FastAPI + agent + ETL scripts).
# Multi-stage: build wheels in a full image, run in a slim one as a non-root user.

FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/mariusrang/ModelAtlas" \
      org.opencontainers.image.description="ModelAtlas API: knowledge graph of the open AI model ecosystem"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /srv

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels \
    && useradd --create-home --uid 10001 atlas

COPY app ./app
COPY scripts ./scripts
COPY config ./config

USER atlas
EXPOSE 8000

# --root-path makes Swagger work when Nginx serves the API under /api
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--root-path", "/api", "--proxy-headers"]
