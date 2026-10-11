# Secure local image (stub-only; no secrets in build args/env/layers).
# Base pinned by digest (reviewed index; deployment builds and verifies linux/amd64).
FROM python:3.13.13-alpine@sha256:420cd0bf0f3998275875e02ecd5808168cf0843cbb4d3c536432f729247b2acc

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/app/src \
    UPSTREAM_MODE=stub

RUN apk upgrade --no-cache \
    && addgroup -g 10001 appuser && adduser -D -u 10001 -G appuser appuser
WORKDIR /srv/app

COPY app/requirements-runtime.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip uninstall -y pip setuptools wheel \
    && rm -rf /root/.cache /tmp/*

COPY --chown=appuser:appuser app/src ./src

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "--factory", "gateway.web:create_app", "--host", "0.0.0.0", "--port", "8000"]
