#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f ".env" ]]; then
  echo "[안내] .env 파일이 이미 있습니다. 그대로 사용합니다."
  exit 0
fi

cp .env.example .env

if command -v openssl >/dev/null 2>&1; then
  key="$(openssl rand -hex 32)"
elif command -v python3 >/dev/null 2>&1; then
  key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
else
  key=""
fi

if [[ -n "${key:-}" ]]; then
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${key}|" .env
fi

chmod 600 .env || true

echo "[완료] .env 파일을 만들었습니다."
echo "다음 단계:"
echo "1) .env에서 POSTGRES_PASSWORD, CONTACT_EMAIL 값을 원하는 값으로 바꾸기"
echo "2) docker compose up -d --build"
