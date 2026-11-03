#!/usr/bin/env bash
# 已由 python -m pipeline prepare-audio 取代（多季 / 流复制 / 时长校验）
# 保留仅为兼容旧习惯： ./convert_m4a.sh season2
set -euo pipefail
cd "$(dirname "$0")"
exec whisperASR_env/bin/python -m pipeline prepare-audio --season "${1:-season1}" --episodes all
