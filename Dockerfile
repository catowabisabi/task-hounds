FROM node:20-alpine AS frontend-builder

WORKDIR /app/ui/web
COPY ui/web/package.json ui/web/package-lock.json* ./
RUN npm ci
COPY ui/web/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/core
ENV POWER_TEAMS_DB=/app/data/power_teams.db

WORKDIR /app

COPY requirements.txt pyproject.toml README.md LICENSE ./
COPY core/ core/
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir .

COPY --from=frontend-builder /app/ui/web/dist/ ui/web/dist/

RUN mkdir -p /app/data /app/core/runtime/logs

VOLUME ["/app/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/agents', timeout=3)"

CMD ["python", "-m", "task_hounds_api", "--host", "0.0.0.0", "--port", "8765"]
