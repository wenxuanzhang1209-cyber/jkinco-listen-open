FROM node:22-alpine AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm ci && npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY jkinco_*.py ./
COPY report_templates.py ./
COPY templates ./templates
COPY assets ./assets
COPY --from=frontend /web/dist ./frontend/dist
ENV JKINCO_HISTORY_DIR=/data/history
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=8s --retries=5 --start-period=45s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=5)" || exit 1
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--backlog", "2048", "--timeout-graceful-shutdown", "45", "--no-access-log"]
