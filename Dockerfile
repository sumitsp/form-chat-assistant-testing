FROM node:22-bookworm-slim

# Install Python runtime for FastAPI backend + supervisor (process manager)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    supervisor \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Node deps (frontend — package.json at repo root)
COPY package.json package-lock.json* ./
RUN npm ci

# Python deps (backend)
COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
  && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
  && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# App source
COPY frontend ./frontend
COPY backend ./backend
COPY supervisord.conf ./supervisord.conf

EXPOSE 5173
EXPOSE 8080
EXPOSE 8090

# Single container, three independently-restartable processes (api, pricing,
# frontend) managed by supervisord. Pricing auto-restarts on crash; restart it
# manually without bouncing the others via:
#   docker exec <container> supervisorctl restart pricing
CMD ["supervisord", "-c", "/app/supervisord.conf"]
