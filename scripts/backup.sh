#!/usr/bin/env bash
# 전체 프로젝트 스냅샷(코드 + 설정 파일) 백업
# 사용: bash scripts/backup.sh [BACKUP_DIR(default: ~/backups)]

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "${PROJECT_DIR}")"
PARENT_DIR="$(dirname "${PROJECT_DIR}")"
BACKUP_DIR="${1:-${HOME}/backups}"
STAMP="$(date +'%Y%m%d_%H%M%S')"
NAME="${PROJECT_NAME}_${STAMP}.tar.gz"

mkdir -p "${BACKUP_DIR}"

echo "📦 백업 생성: ${BACKUP_DIR}/${NAME}"
# 불필요물 제외: __pycache__, .git, .venv 등
tar \
  --exclude='*.pyc' \
  --exclude='__pycache__' \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.mypy_cache' \
  --exclude='.pytest_cache' \
  --exclude='.idea' \
  --exclude='.vscode' \
  --exclude='*.log' \
  --exclude='*.tmp' \
  --exclude='.DS_Store' \
  -czf "${BACKUP_DIR}/${NAME}" \
  -C "${PARENT_DIR}" "${PROJECT_NAME}"

echo "✅ 완료: ${BACKUP_DIR}/${NAME}"
ls -lh "${BACKUP_DIR}/${NAME}"
