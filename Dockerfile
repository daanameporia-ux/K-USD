# syntax=docker/dockerfile:1

# --- frontend build -------------------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
# Optional build secret `extra_ca`: a CA bundle for builds behind a TLS-intercepting proxy (not needed normally).
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- backend application ----------------------------------------------------------------
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/backend
COPY backend/pyproject.toml backend/requirements.lock ./
COPY backend/app ./app
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -c requirements.lock .
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY scripts ./scripts
COPY --from=frontend /fe/dist /srv/frontend
ENV FRONTEND_DIR=/srv/frontend
RUN useradd --system --uid 10001 kusd
USER kusd
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
# Migrations are applied before the app starts; uvicorn waits up to 25 s for in-flight requests on SIGTERM.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:build --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 20 --no-access-log"]

# --- Caddy with the static frontend --------------------------------------------------------
FROM caddy:2-alpine AS caddy
COPY --from=frontend /fe/dist /srv/frontend
COPY Caddyfile /etc/caddy/Caddyfile
