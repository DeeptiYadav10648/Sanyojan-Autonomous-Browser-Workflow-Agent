# syntax=docker/dockerfile:1

FROM node:24.19.0-bookworm-slim AS frontend-build
WORKDIR /app
ENV CI=true
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
RUN npm ci --include=dev --no-audit --no-fund
COPY frontend/ ./frontend/
RUN npm run build

FROM python:3.11.9-slim-bookworm AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PATH="/app/.venv/bin:${PATH}"

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev \
    && .venv/bin/python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright \
    && rm -rf /var/lib/apt/lists/* /root/.cache

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data/reports \
    && chown -R app:app /app/data

COPY backend/ ./backend/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
USER app:app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request; health = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)); raise SystemExit(0 if health.get('status') == 'ok' and health.get('browser_available') is True else 1)"]
CMD ["python", "-m", "uvicorn", "sanyojan.app:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
