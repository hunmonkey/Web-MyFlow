#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f ".env" ]]; then
  echo "[중단] .env 파일이 없습니다. 먼저 '.env.example'을 복사해 '.env'를 만들어 주세요."
  echo "예시: cp .env.example .env"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "[중단] 'docker compose' 명령을 찾지 못했습니다."
  exit 1
fi

echo "[1/3] 이미지 빌드 + 컨테이너 기동"
docker compose up -d --build

echo "[2/3] 컨테이너 상태 확인"
docker compose ps

echo "[3/3] 웹 컨테이너 health 상태 확인"
health="$(docker inspect --format '{{.State.Health.Status}}' Web-MyFlow 2>/dev/null || true)"
if [[ "$health" == "healthy" ]]; then
  echo "[완료] Web-MyFlow health=healthy"
else
  echo "[안내] 아직 healthy가 아닐 수 있습니다. 10~30초 뒤 아래 명령으로 다시 확인해 주세요."
  echo "docker inspect --format '{{.State.Health.Status}}' Web-MyFlow"
fi

scheduler_status="$(docker inspect --format '{{.State.Status}}' BackupScheduler-MyFlow 2>/dev/null || true)"
if [[ "$scheduler_status" == "running" ]]; then
  echo "[완료] BackupScheduler-MyFlow status=running"
else
  echo "[안내] BackupScheduler-MyFlow 상태를 확인해 주세요."
  echo "docker inspect --format '{{.State.Status}}' BackupScheduler-MyFlow"
fi
