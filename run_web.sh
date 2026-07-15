#!/usr/bin/env bash
# Fitness Agent 웹앱 — 안정 실행 (Connection Error / segfault 방지)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONDA_BASE="${CONDA_BASE:-/opt/homebrew/Caskroom/miniconda/base}"
if [[ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]]; then
  # shellcheck disable=SC1091
  source "$CONDA_BASE/etc/profile.d/conda.sh"
elif [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/miniconda3/etc/profile.d/conda.sh"
fi

if conda env list | grep -qE '^day15\s'; then
  conda activate day15
elif conda env list | grep -qE '^day6\s'; then
  conda activate day6
fi

PY_VER="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$PY_VER" == "3.13" ]]; then
  echo "오류: Python 3.13은 Streamlit segfault 원인입니다. conda activate day15 후 다시 실행하세요."
  exit 1
fi

MOBILE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mobile|--lan)
      MOBILE=true
      shift
      ;;
    --install)
      pip install -r requirements.txt
      shift
      ;;
    *)
      echo "사용법: $0 [--install] [--mobile|--lan]"
      exit 1
      ;;
  esac
done

export STREAMLIT_SERVER_FILE_WATCHER_TYPE=none

# pyarrow의 기본 mimalloc 할당자가 최신 macOS(예: 26.x)의 스레드 로컬 스토리지 구현과
# 충돌하며 세그폴트(segmentation fault, exit 139)를 유발하는 알려진 문제가 있습니다.
# (크래시 스택: mi_thread_init -> MimallocAllocator::AllocateAligned -> ...)
# 시스템 malloc을 쓰도록 강제해 이 문제를 회피합니다.
export ARROW_DEFAULT_MEMORY_POOL=system

detect_lan_ip() {
  local ip=""
  for iface in en0 en1 bridge0; do
    ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
    if [[ -n "$ip" ]]; then
      echo "$ip"
      return
    fi
  done
  python - <<'PY' 2>/dev/null || echo "알 수 없음"
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("8.8.8.8", 80))
    print(s.getsockname()[0])
finally:
    s.close()
PY
}

if [[ "$MOBILE" == true ]]; then
  SERVER_ADDRESS="0.0.0.0"
  LAN_IP="$(detect_lan_ip)"
else
  SERVER_ADDRESS="localhost"
  LAN_IP=""
fi

echo "Python: $(python --version)"
echo "PC 브라우저: http://localhost:8501"
if [[ "$MOBILE" == true ]]; then
  echo ""
  echo "📱 폰 브라우저 접속 (같은 Wi-Fi 필요):"
  echo "   http://${LAN_IP}:8501"
  echo ""
  echo "※ Mac 방화벽에서 Python/Streamlit 연결 허용이 필요할 수 있습니다."
  echo "   시스템 설정 → 네트워크 → 방화벽 → 옵션"
fi
echo "종료: Ctrl+C"
echo ""

exec streamlit run app.py \
  --server.port 8501 \
  --server.address "$SERVER_ADDRESS" \
  --server.fileWatcherType none \
  --server.runOnSave false \
  --browser.gatherUsageStats false
