#!/usr/bin/env bash
set -euo pipefail

if [ -z "${CTYUN_BROWSER_WORKER_TOKEN:-}" ]; then
  CTYUN_BROWSER_WORKER_TOKEN="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  export CTYUN_BROWSER_WORKER_TOKEN
fi

node /app/worker/server.mjs & worker_pid=$!
/app/ctyun-manager & app_pid=$!
cleanup() {
	  kill -TERM "$app_pid" >/dev/null 2>&1 || true
  kill -TERM "$worker_pid" >/dev/null 2>&1 || true
	  wait "$app_pid" >/dev/null 2>&1 || true
  wait "$worker_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT TERM INT
wait "$app_pid"
