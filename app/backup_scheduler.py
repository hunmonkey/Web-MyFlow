from __future__ import annotations

import os
import time
from datetime import datetime

from app import create_app
from app.backup_service import (
    BACKUP_TYPE_LABELS,
    BACKUP_TYPES,
    ensure_default_backup_schedules,
    is_schedule_due,
    run_backup,
)
from app.extensions import db


def _to_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
        return parsed if parsed > 0 else default
    except ValueError:
        return default


def _tick() -> None:
    from app.models import BackupSchedule

    now = datetime.now().replace(second=0, microsecond=0)
    ensure_default_backup_schedules()
    rows = (
        BackupSchedule.query.filter(BackupSchedule.backup_type.in_(BACKUP_TYPES))
        .order_by(BackupSchedule.backup_type.asc())
        .all()
    )

    for row in rows:
        if not is_schedule_due(row, now):
            continue

        label = BACKUP_TYPE_LABELS.get(row.backup_type, row.backup_type)
        try:
            backup_file = run_backup(row.backup_type, now=now)
            row.last_run_at = now
            db.session.commit()
            print(f"[자동백업] 성공: {label} -> {backup_file}", flush=True)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            print(f"[자동백업] 실패: {label} -> {exc}", flush=True)


def main() -> None:
    env = os.environ.get("FLASK_ENV", "production")
    app = create_app(env)
    interval_seconds = _to_int(os.environ.get("BACKUP_SCHEDULER_INTERVAL_SECONDS"), 30)

    print(f"[자동백업] 스케줄러 시작 (체크 주기: {interval_seconds}초)", flush=True)
    with app.app_context():
        ensure_default_backup_schedules()

    while True:
        with app.app_context():
            _tick()
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
