#!/usr/bin/env bash
set -euo pipefail

systemctl restart ctyun-manager
systemctl --no-pager --full status ctyun-manager
