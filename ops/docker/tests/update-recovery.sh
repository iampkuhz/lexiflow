#!/bin/sh
# 真实发行生命周期入口。只有受控 Harness lease 接入后才可触及 Docker 数据。
set -eu
blocked() {
  printf '{"status":"BLOCKED","checks_run":0,"failures":0,"errors":0,"skipped":0,"reason":"%s"}\n' "$1"
  exit 3
}
if [ -z "${LEXIFLOW_LIFECYCLE_LEASE:-}" ]; then
  blocked LIFECYCLE_LEASE_UNAVAILABLE
fi
if [ ! -f "$LEXIFLOW_LIFECYCLE_LEASE" ] || [ -L "$LEXIFLOW_LIFECYCLE_LEASE" ]; then
  blocked LIFECYCLE_LEASE_INVALID
fi
# Lease 的结构与签发由 QLT-2004 管理；环境变量中的路径不构成运行授权。
blocked LIFECYCLE_LEASE_ADAPTER_UNAVAILABLE
