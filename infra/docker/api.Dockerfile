# Build context: repository root (see docker-compose.yml).

# --- Web UI build ------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

# --- API (also serves the built UI, same origin) -------------------------------
FROM python:3.11-slim

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/README.md ./
COPY apps/api/app ./app

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

COPY --from=web /web/dist ./web-dist
ENV WEB_DIST_DIR=/app/web-dist

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
