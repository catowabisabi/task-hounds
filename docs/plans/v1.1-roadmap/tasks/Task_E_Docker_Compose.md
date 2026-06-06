# Task E — Docker Compose

## Goal
Containerize all components so the entire stack can be launched with a single `docker compose up`.

## Scope
- `Dockerfile` (existing) — update for DATABASE_URL support and health checks
- `Dockerfile.api` (new) — separate API service
- `docker-compose.yml` (new) — orchestration
- `.dockerignore` (new) — reduce image size

## Rationale
This task has **no dependencies** and can start immediately. Early Docker setup validates the deployment story before the PostgreSQL migration (Task B) is complete.

---

## Deliverables

### 1. `docker-compose.yml`

```yaml
version: '3.9'

services:
  # ── API Server ──────────────────────────────────────────
  api:
    build:
      context: .
      dockerfile: Dockerfile.api
    container_name: power-teams-api
    ports:
      - "8765:8765"
    environment:
      - DATABASE_URL=postgresql://postgres:${POSTGRES_PASSWORD:-password}@db:5432/power_teams
      - RUNTIME_DIR=/app/runtime
      - PYTHONUNBUFFERED=1
    volumes:
      - ./data:/app/data
      - ./runtime:/app/runtime
      - ./config:/app/config
      - ./workspace:/app/workspace
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8765/api/loop/status')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    networks:
      - power-teams-net

  # ── Agent Runner ─────────────────────────────────────────
  agent-runner:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: power-teams-runner
    command: >
      python -m power_teams.mvp.runner
      --auto-release
      --manager-interval 5
      --worker-poll 3
    environment:
      - DATABASE_URL=postgresql://postgres:${POSTGRES_PASSWORD:-password}@db:5432/power_teams
      - RUNTIME_DIR=/app/runtime
      - PYTHONUNBUFFERED=1
    volumes:
      - ./data:/app/data
      - ./runtime:/app/runtime
      - ./config:/app/config
      - ./workspace:/app/workspace
    depends_on:
      db:
        condition: service_healthy
      api:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - power-teams-net

  # ── PostgreSQL ────────────────────────────────────────────
  db:
    image: postgres:16-alpine
    container_name: power-teams-db
    environment:
      - POSTGRES_DB=power_teams
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-password}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./data/schema.pg.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d power_teams"]
      interval: 5s
      timeout: 5s
      retries: 5
      start_period: 5s
    restart: unless-stopped
    networks:
      - power-teams-net

  # ── Optional: Redis (Phase 2 enhancement) ────────────────
  # Uncomment if implementing Redis pub/sub
  # redis:
  #   image: redis:7-alpine
  #   container_name: power-teams-redis
  #   ports:
  #     - "6379:6379"
  #   volumes:
  #     - redisdata:/data
  #   healthcheck:
  #     test: ["CMD", "redis-cli", "ping"]
  #     interval: 10s
  #     timeout: 5s
  #     retries: 3
  #   networks:
  #     - power-teams-net

networks:
  power-teams-net:
    driver: bridge

volumes:
  pgdata:
  # redisdata:
```

---

### 2. `Dockerfile.api` (new)

```dockerfile
# Stage 1: Builder
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY core/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application
COPY core/ ./core/
COPY data/ ./data/
COPY config/ ./config/

# Non-root user for security
RUN useradd --create-home --shell /bin/bash agent && \
    chown -R agent:agent /app
USER agent

EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/api/loop/status')"

CMD ["python", "core/api/fastapi_server.py"]
```

---

### 3. `Dockerfile` (update — existing file)

Changes needed:
1. Add `psycopg2-binary` to requirements for PostgreSQL support
2. Add `DATABASE_URL` env var support
3. Add `POSTGRES_PASSWORD` env var support
4. Update health check to validate DB connection
5. Add `--health-cmd` if using `--health-start-period` in docker-compose

```dockerfile
# Add to existing Dockerfile, after the existing CMD:
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "from power_teams.db import test_connection; test_connection()"
```

---

### 4. `.dockerignore` (new)

```
# Git
.git
.gitignore

# Python
__pycache__
*.py[cod]
*.egg-info
.eggs
dist
build

# Virtual environments
venv
.venv
env
.env

# Testing
.pytest_cache
.pytest_tmp_*
tmp_pytest
tests/

# IDE
.vscode
.idea
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Runtime (should be volume-mounted, not baked in)
runtime/
data/*.db

# Docs and guides
docs/
README.md
LICENSE

# UI (build separately)
ui/
```

---

### 5. `.env.docker` (new — for local dev with Docker Compose)

```bash
# Database
POSTGRES_PASSWORD=your_secure_password_here

# Paths (mounted volumes)
DATA_DIR=./data
RUNTIME_DIR=./runtime
CONFIG_DIR=./config
WORKSPACE_DIR=./workspace

# Optional: Redis (Phase 2)
# REDIS_URL=redis://redis:6379/0
```

---

## Usage

### First time setup
```bash
# 1. Copy environment
cp .env.docker .env

# 2. Build images
docker compose build

# 3. Start (creates DB schema on first run)
docker compose up -d

# 4. Watch logs
docker compose logs -f

# 5. Stop
docker compose down
```

### With existing SQLite data
```bash
# Migrate first (Task B must be complete)
python migrations/001_sqlite_to_pg.py

# Then start with PostgreSQL
docker compose up -d
```

### Scale workers (future)
```bash
# Run multiple agent runners
docker compose up -d --scale agent-runner=3
```

## Files to Create
- `Dockerfile.api` (new)
- `docker-compose.yml` (new)
- `.dockerignore` (new)
- `.env.docker` (new)

## Files to Modify
- `Dockerfile` (existing) — add health check, psycopg2 support
- `core/requirements.txt` — add `psycopg2-binary`

## Effort
1 person, 3–5 days (can start immediately, no dependencies)