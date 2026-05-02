#!/bin/sh
set -eu

if [ "${WAIT_FOR_DB:-1}" = "1" ]; then
  echo "[시작 준비] 데이터베이스 연결을 기다립니다."
  python - <<'PY'
import os
import sys
import time

import psycopg2

db_url = os.environ.get("DATABASE_URL", "")
if not db_url:
    print("[정보] DATABASE_URL이 비어 있어 DB 대기를 건너뜁니다.")
    sys.exit(0)

for attempt in range(1, 31):
    try:
        conn = psycopg2.connect(db_url)
        conn.close()
        print("[시작 준비] 데이터베이스 연결 확인 완료")
        break
    except Exception:
        if attempt == 30:
            print("[시작 실패] DB 연결 대기 시간(60초)을 초과했습니다.", file=sys.stderr)
            raise
        print(f"[대기] DB 연결 재시도 {attempt}/30")
        time.sleep(2)
PY
fi

exec "$@"
