#!/usr/bin/env bash
# 현재 venv/시스템의 pip 패키지를 requirements.txt로 동결
# 사용: bash scripts/freeze_deps.sh

set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# venv 우선
if [[ -x "${PROJECT_DIR}/.venv/bin/pip" ]]; then
  PIP="${PROJECT_DIR}/.venv/bin/pip"
else
  PIP="python3 -m pip"
fi

echo "📌 pip freeze → requirements.txt (${PIP})"
# pip/setuptools/wheel, pkg-resources 제거
${PIP} freeze \
  | grep -vE '^(pip==|setuptools==|wheel==|pkg-resources==0\.0\.0)$' \
  > "${PROJECT_DIR}/requirements.txt"

echo "✅ 동결 완료: ${PROJECT_DIR}/requirements.txt"
sed -n '1,20p' "${PROJECT_DIR}/requirements.txt"
