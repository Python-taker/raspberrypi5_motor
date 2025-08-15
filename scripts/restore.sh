#!/usr/bin/env bash
# 백업 복원
# 사용:
#   1) 최신 백업 자동: bash scripts/restore.sh
#   2) 특정 파일 지정: bash scripts/restore.sh /path/to/ssafy_project_YYYYmmdd_HHMMSS.tar.gz [TARGET_DIR(default: ~)]

set -euo pipefail

BACKUP_DIR="${HOME}/backups"

if [[ $# -ge 1 ]]; then
  TARBALL="$1"
else
  TARBALL="$(ls -t "${BACKUP_DIR}"/ssafy_project_*.tar.gz 2>/dev/null | head -n1 || true)"
fi

TARGET_DIR="${2:-${HOME}}"

if [[ -z "${TARBALL:-}" || ! -f "${TARBALL}" ]]; then
  echo "❌ 백업 파일을 찾을 수 없습니다."
  echo "   - 디폴트 경로: ${BACKUP_DIR}/ssafy_project_*.tar.gz"
  echo "   - 또는 파일을 인자로 지정하세요: restore.sh /path/to/file.tar.gz"
  exit 1
fi

echo "🧩 복원: ${TARBALL} → ${TARGET_DIR}"
tar -xzf "${TARBALL}" -C "${TARGET_DIR}"

# 복원된 폴더명 안내
RESTORED_NAME="$(tar -tzf "${TARBALL}" | head -n1 | cut -d/ -f1)"
echo "✅ 복원 완료: ${TARGET_DIR}/${RESTORED_NAME}"
echo "ℹ️ 가상환경은 포함되지 않습니다(.venv 제외). 아래 순서로 복원하세요:"
echo "   cd ${TARGET_DIR}/${RESTORED_NAME}"
echo "   python -m venv .venv && source .venv/bin/activate"
echo "   pip install -r requirements.txt"
