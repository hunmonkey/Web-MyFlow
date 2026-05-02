from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path

from flask import current_app
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.extensions import db
from app.models import BackupSchedule

BACKUP_TYPE_DB = "DB"
BACKUP_TYPE_DATA = "DATA"
BACKUP_TYPES = (BACKUP_TYPE_DB, BACKUP_TYPE_DATA)
BACKUP_TYPE_LABELS = {
    BACKUP_TYPE_DB: "DB",
    BACKUP_TYPE_DATA: "일반자료",
}

SCHEDULE_FORMAT_MASK_V1 = "mask1"

WEEKDAY_LABELS = {
    0: "월요일",
    1: "화요일",
    2: "수요일",
    3: "목요일",
    4: "금요일",
    5: "토요일",
    6: "일요일",
}
WEEKDAY_SHORT_LABELS = {
    0: "월",
    1: "화",
    2: "수",
    3: "목",
    4: "금",
    5: "토",
    6: "일",
}
WEEKDAY_OPTIONS = (
    (6, "일"),
    (0, "월"),
    (1, "화"),
    (2, "수"),
    (3, "목"),
    (4, "금"),
    (5, "토"),
)
WEEKDAY_MASK_ALL = (1 << 7) - 1
WEEKDAY_MASK_DEFAULT = 1 << 0  # 월요일

_RUN_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")


def normalize_backup_type(value: str | None) -> str:
    return str(value or "").strip().upper()


def validate_run_time(run_time: str | None) -> bool:
    return bool(_RUN_TIME_PATTERN.fullmatch(str(run_time or "").strip()))


def parse_run_time(run_time: str) -> tuple[int, int] | None:
    if not validate_run_time(run_time):
        return None
    hour_text, minute_text = run_time.split(":", 1)
    return int(hour_text), int(minute_text)


def normalize_weekday_mask(value: int | None) -> int:
    try:
        mask = int(value if value is not None else 0)
    except (TypeError, ValueError):
        return 0
    if mask < 0:
        return 0
    return mask & WEEKDAY_MASK_ALL


def weekday_mask_from_values(values: list[int]) -> int:
    mask = 0
    for value in values:
        if 0 <= value <= 6:
            mask |= 1 << value
    return normalize_weekday_mask(mask)


def weekday_mask_to_values(mask: int) -> list[int]:
    normalized = normalize_weekday_mask(mask)
    return [day for day in range(7) if normalized & (1 << day)]


def is_weekday_selected(mask: int, day: int) -> bool:
    if day < 0 or day > 6:
        return False
    return bool(normalize_weekday_mask(mask) & (1 << day))


def _upgrade_schedule_format(row: BackupSchedule) -> bool:
    repeat_type = str(row.repeat_type or "").strip().lower()
    changed = False

    if repeat_type == SCHEDULE_FORMAT_MASK_V1:
        next_mask = normalize_weekday_mask(row.weekday)
        if next_mask == 0:
            next_mask = WEEKDAY_MASK_DEFAULT
        if row.weekday != next_mask:
            row.weekday = next_mask
            changed = True
        return changed

    if repeat_type == "daily":
        next_mask = WEEKDAY_MASK_ALL
    elif repeat_type == "weekly":
        try:
            legacy_weekday = int(row.weekday)
        except (TypeError, ValueError):
            legacy_weekday = 0
        if legacy_weekday < 0 or legacy_weekday > 6:
            legacy_weekday = 0
        next_mask = 1 << legacy_weekday
    else:
        next_mask = normalize_weekday_mask(row.weekday)
        if next_mask == 0:
            next_mask = WEEKDAY_MASK_DEFAULT

    row.weekday = next_mask
    row.repeat_type = SCHEDULE_FORMAT_MASK_V1
    return True


def ensure_default_backup_schedules() -> dict[str, BackupSchedule]:
    schedules: dict[str, BackupSchedule] = {}
    changed = False

    for backup_type in BACKUP_TYPES:
        row = BackupSchedule.query.filter_by(backup_type=backup_type).first()
        if row is None:
            row = BackupSchedule(
                backup_type=backup_type,
                is_enabled=False,
                repeat_type=SCHEDULE_FORMAT_MASK_V1,
                weekday=WEEKDAY_MASK_DEFAULT,
                run_time="03:00",
            )
            db.session.add(row)
            changed = True
        elif _upgrade_schedule_format(row):
            changed = True
        schedules[backup_type] = row

    if changed:
        db.session.commit()

    return schedules


def is_schedule_due(schedule: BackupSchedule, now: datetime) -> bool:
    if not schedule.is_enabled:
        return False

    parsed_time = parse_run_time(schedule.run_time)
    if parsed_time is None:
        return False

    run_hour, run_minute = parsed_time
    if now.hour != run_hour or now.minute != run_minute:
        return False

    weekday_mask = normalize_weekday_mask(schedule.weekday)
    if weekday_mask == 0:
        return False
    if not is_weekday_selected(weekday_mask, now.weekday()):
        return False

    last_run_at = schedule.last_run_at
    if (
        last_run_at
        and last_run_at.year == now.year
        and last_run_at.month == now.month
        and last_run_at.day == now.day
        and last_run_at.hour == now.hour
        and last_run_at.minute == now.minute
    ):
        return False

    return True


def _create_backup_slot(backup_type: str, now: datetime | None = None) -> Path:
    normalized_type = normalize_backup_type(backup_type)
    if normalized_type not in BACKUP_TYPES:
        raise RuntimeError(f"지원하지 않는 백업 종류입니다: {backup_type}")

    backup_root = Path(current_app.config.get("BACKUP_ROOT_PATH", "/mnt/user/GRM-BACKUP/BK-Web-MyFlow"))
    moment = now or datetime.now()
    date_folder = moment.strftime("%Y%m%d")
    date_dir = backup_root / normalized_type / date_folder
    date_dir.mkdir(parents=True, exist_ok=True)

    seq = 1
    while True:
        seq_dir = date_dir / f"{seq:02d}"
        if not seq_dir.exists():
            seq_dir.mkdir(parents=True, exist_ok=False)
            return seq_dir
        seq += 1


def _backup_postgres_to_gzip(slot_dir: Path, now: datetime | None = None) -> Path:
    db_uri = str(current_app.config.get("SQLALCHEMY_DATABASE_URI") or "").strip()
    if not db_uri:
        raise RuntimeError("DB 연결 정보가 비어 있어 백업을 진행할 수 없습니다.")

    try:
        parsed = make_url(db_uri)
    except ArgumentError as exc:
        raise RuntimeError("DB 연결 주소 형식을 해석하지 못했습니다.") from exc

    driver = (parsed.drivername or "").split("+", 1)[0]
    if driver != "postgresql":
        raise RuntimeError("현재 DB 백업은 PostgreSQL만 지원합니다.")

    if not parsed.username or not parsed.host or not parsed.database:
        raise RuntimeError("DB 접속 정보(사용자/호스트/DB이름)가 부족합니다.")

    moment = now or datetime.now()
    timestamp = moment.strftime("%Y%m%d_%H%M%S")
    sql_path = slot_dir / f"db_{timestamp}.sql"
    gz_path = slot_dir / f"db_{timestamp}.sql.gz"

    cmd = [
        "pg_dump",
        "--host",
        str(parsed.host),
        "--port",
        str(parsed.port or 5432),
        "--username",
        str(parsed.username),
        "--no-owner",
        "--clean",
        "--if-exists",
        "--encoding=UTF8",
        str(parsed.database),
    ]
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = str(parsed.password)

    try:
        with sql_path.open("wb") as sql_file:
            result = subprocess.run(
                cmd,
                stdout=sql_file,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
    except FileNotFoundError as exc:
        raise RuntimeError("pg_dump 명령을 찾지 못했습니다. 컨테이너 빌드를 다시 진행해 주세요.") from exc

    if result.returncode != 0:
        sql_path.unlink(missing_ok=True)
        stderr_text = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
        raise RuntimeError(stderr_text or "DB 백업 중 알 수 없는 오류가 발생했습니다.")

    with sql_path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    sql_path.unlink(missing_ok=True)
    return gz_path


def _backup_upload_data_to_tar(slot_dir: Path, now: datetime | None = None) -> Path:
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    moment = now or datetime.now()
    timestamp = moment.strftime("%Y%m%d_%H%M%S")
    tar_path = slot_dir / f"data_{timestamp}.tar.gz"

    with tarfile.open(tar_path, mode="w:gz") as tar:
        tar.add(upload_dir, arcname=upload_dir.name)

    return tar_path


def run_backup(backup_type: str, now: datetime | None = None) -> Path:
    normalized_type = normalize_backup_type(backup_type)
    slot_dir = _create_backup_slot(normalized_type, now=now)

    try:
        if normalized_type == BACKUP_TYPE_DB:
            return _backup_postgres_to_gzip(slot_dir, now=now)
        if normalized_type == BACKUP_TYPE_DATA:
            return _backup_upload_data_to_tar(slot_dir, now=now)
        raise RuntimeError(f"지원하지 않는 백업 종류입니다: {backup_type}")
    except Exception:
        if slot_dir.exists() and not any(slot_dir.iterdir()):
            slot_dir.rmdir()
        raise
