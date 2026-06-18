FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/app/.playwright \
    CTYUN_MANAGER_DB=/app/data/ctyun-manager.db \
    CTYUN_MANAGER_PORT=8000 \
    CTYUN_BROWSER_HEADFUL=1 \
    HOME=/app/data/home \
    DISPLAY=:99

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      fluxbox \
      fonts-noto-cjk \
      iproute2 \
      novnc \
      procps \
      x11vnc \
      xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --prefer-binary --timeout 120 --retries 10 -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY start.sh ./start.sh

RUN chmod +x /app/start.sh \
    && mkdir -p /app/data/home /app/.playwright \
    && groupadd --system --gid 10001 ctyun-manager \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app/data/home --shell /usr/sbin/nologin ctyun-manager \
    && chown -R ctyun-manager:ctyun-manager /app/data /app/.playwright

USER ctyun-manager

EXPOSE 8000
VOLUME ["/app/data"]

CMD ["/app/start.sh"]
