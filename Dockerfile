FROM golang:1.26-bookworm AS go-build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
ARG VERSION=dev
ARG COMMIT=unknown
ARG BUILD_TIME=unknown
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X github.com/dayou0168/ctyun-manager/internal/buildinfo.Version=${VERSION} -X github.com/dayou0168/ctyun-manager/internal/buildinfo.Commit=${COMMIT} -X github.com/dayou0168/ctyun-manager/internal/buildinfo.BuildTime=${BUILD_TIME}" -o /out/ctyun-manager ./cmd/ctyun-manager-go

FROM mcr.microsoft.com/playwright:v1.55.1-noble
ENV NODE_ENV=production \
    CTYUN_MANAGER_ROOT=/app \
    CTYUN_MANAGER_DB=/app/data/ctyun-manager.db \
    CTYUN_MANAGER_GO_HOST=0.0.0.0 \
    CTYUN_MANAGER_GO_PORT=8000 \
    CTYUN_MANAGER_DB_READ_ONLY=0 \
    CTYUN_BROWSER_WORKER_URL=http://127.0.0.1:18080 \
    CTYUN_BROWSER_WORKER_PORT=18080 \
    CTYUN_BROWSER_HEADLESS=1 \
    HOME=/app/data/home
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl tini fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
COPY --from=go-build /out/ctyun-manager /app/ctyun-manager
COPY app/static /app/app/static
COPY install-l2tp-server.sh /app/install-l2tp-server.sh
COPY third_party/xl2tpd-v1.3.20.tar.gz /app/third_party/xl2tpd-v1.3.20.tar.gz
COPY worker/package.json worker/pnpm-lock.yaml worker/server.mjs worker/recharge.mjs /app/worker/
RUN npm install --global pnpm@10.15.1 --no-audit --no-fund \
    && cd /app/worker && pnpm install --prod --frozen-lockfile \
    && mkdir -p /app/data/home \
    && groupadd --system --gid 10001 ctyun-manager \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app/data/home --shell /usr/sbin/nologin ctyun-manager \
    && chown -R ctyun-manager:ctyun-manager /app/data /app/worker
COPY start-go.sh /app/start-go.sh
RUN chmod 755 /app/ctyun-manager /app/start-go.sh /app/install-l2tp-server.sh
USER ctyun-manager
EXPOSE 8000
VOLUME ["/app/data"]
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["/app/start-go.sh"]
