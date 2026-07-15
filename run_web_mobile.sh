#!/usr/bin/env bash
# 폰 브라우저 접속용 — LAN(0.0.0.0) 바인딩
exec "$(dirname "$0")/run_web.sh" --mobile "$@"
