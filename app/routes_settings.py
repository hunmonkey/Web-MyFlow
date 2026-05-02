from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.extensions import db
from app.models import Profile, User, UserFormatSetting

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")

DATE_FORMAT_OPTIONS = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "YYYY.MM.DD": "%Y.%m.%d",
    "MM/DD/YYYY": "%m/%d/%Y",
}
DATETIME_FORMAT_OPTIONS = {
    "YYYY-MM-DD HH:mm": "%Y-%m-%d %H:%M",
    "YYYY.MM.DD HH:mm": "%Y.%m.%d %H:%M",
    "MM/DD/YYYY hh:mm A": "%m/%d/%Y %I:%M %p",
}
CURRENCY_SYMBOL_OPTIONS = {"원", "₩", "KRW"}
THEME_OPTIONS = {"system", "light", "dark"}


@settings_bp.before_request
def _require_login():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=request.url))
    return None


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _require_admin():
    if not current_user.is_admin:
        abort(403)


def _validate_password_strength(password: str) -> str | None:
    if len(password or "") < 10:
        return "비밀번호는 최소 10자 이상으로 입력해 주세요."
    if not re.search(r"[A-Za-z]", password or "") or not re.search(r"[0-9]", password or ""):
        return "비밀번호는 영문과 숫자를 함께 포함해 주세요."
    return None


def _create_backup_slot(category: str) -> Path:
    backup_root = Path(current_app.config.get("BACKUP_ROOT_PATH", "/mnt/user/GRM-BACKUP/BK-Web-MyFlow"))
    date_folder = datetime.now().strftime("%Y%m%d")
    date_dir = backup_root / category / date_folder
    date_dir.mkdir(parents=True, exist_ok=True)

    seq = 1
    while True:
        seq_dir = date_dir / f"{seq:02d}"
        if not seq_dir.exists():
            seq_dir.mkdir(parents=True, exist_ok=False)
            return seq_dir
        seq += 1


def _backup_postgres_to_gzip(slot_dir: Path) -> Path:
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

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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


def _backup_upload_data_to_tar(slot_dir: Path) -> Path:
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tar_path = slot_dir / f"data_{timestamp}.tar.gz"

    with tarfile.open(tar_path, mode="w:gz") as tar:
        tar.add(upload_dir, arcname=upload_dir.name)

    return tar_path


def _get_or_create_user_format_setting(user_id: int) -> UserFormatSetting:
    setting = UserFormatSetting.query.filter_by(user_id=user_id).first()
    if setting is None:
        setting = UserFormatSetting(user_id=user_id)
        db.session.add(setting)
        db.session.commit()
    return setting


@settings_bp.get("/")
def index():
    profile = Profile.query.order_by(Profile.id.asc()).first()
    return render_template(
        "settings/index.html",
        profile=profile,
    )


@settings_bp.post("/profile")
def profile_update():
    _require_admin()

    profile = Profile.query.order_by(Profile.id.asc()).first()
    if profile is None:
        profile = Profile(
            name="",
            headline="",
            intro="",
            contact_email="",
        )
        db.session.add(profile)

    profile.name = (request.form.get("name") or "").strip() or profile.name or "재훈"
    profile.headline = (request.form.get("headline") or "").strip() or "개인 페이지"
    profile.intro = (request.form.get("intro") or "").strip() or ""
    profile.contact_email = (request.form.get("contact_email") or "").strip()

    db.session.commit()
    flash("환경설정(기본 프로필)을 저장했습니다.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/password")
def password_change():
    current_password = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    if not current_user.check_password(current_password):
        flash("현재 비밀번호가 맞지 않습니다.", "danger")
        return redirect(url_for("settings.index"))

    if new_password != confirm_password:
        flash("새 비밀번호와 확인 비밀번호가 일치하지 않습니다.", "warning")
        return redirect(url_for("settings.index"))

    password_error = _validate_password_strength(new_password)
    if password_error:
        flash(password_error, "warning")
        return redirect(url_for("settings.index"))

    user = db.session.get(User, int(current_user.id))
    if user is None:
        flash("사용자 정보를 찾지 못했습니다.", "danger")
        return redirect(url_for("settings.index"))

    user.set_password(new_password)
    db.session.commit()
    flash("비밀번호를 변경했습니다.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/backup/db")
def backup_db():
    _require_admin()
    slot_dir: Path | None = None
    try:
        slot_dir = _create_backup_slot("DB")
        backup_file = _backup_postgres_to_gzip(slot_dir)
        flash(f"DB 백업이 완료되었습니다: {backup_file}", "success")
    except Exception as exc:  # noqa: BLE001
        if slot_dir and slot_dir.exists() and not any(slot_dir.iterdir()):
            slot_dir.rmdir()
        flash(f"DB 백업에 실패했습니다: {exc}", "danger")
    return redirect(url_for("settings.index"))


@settings_bp.post("/backup/data")
def backup_data():
    _require_admin()
    slot_dir: Path | None = None
    try:
        slot_dir = _create_backup_slot("DATA")
        backup_file = _backup_upload_data_to_tar(slot_dir)
        flash(f"일반자료 백업이 완료되었습니다: {backup_file}", "success")
    except Exception as exc:  # noqa: BLE001
        if slot_dir and slot_dir.exists() and not any(slot_dir.iterdir()):
            slot_dir.rmdir()
        flash(f"일반자료 백업에 실패했습니다: {exc}", "danger")
    return redirect(url_for("settings.index"))


@settings_bp.route("/formats", methods=["GET", "POST"])
def formats_index():
    setting = _get_or_create_user_format_setting(int(current_user.id))

    if request.method == "POST":
        next_date_format = (request.form.get("date_format") or "").strip()
        next_datetime_format = (request.form.get("datetime_format") or "").strip()
        next_currency_symbol = (request.form.get("currency_symbol") or "").strip()
        next_theme = (request.form.get("preferred_theme") or "").strip().lower()

        if next_date_format not in DATE_FORMAT_OPTIONS:
            flash("날짜 표시 형식이 올바르지 않습니다.", "warning")
            return redirect(url_for("settings.formats_index"))
        if next_datetime_format not in DATETIME_FORMAT_OPTIONS:
            flash("날짜+시간 표시 형식이 올바르지 않습니다.", "warning")
            return redirect(url_for("settings.formats_index"))
        if next_currency_symbol not in CURRENCY_SYMBOL_OPTIONS:
            flash("통화 표시 형식이 올바르지 않습니다.", "warning")
            return redirect(url_for("settings.formats_index"))
        if next_theme not in THEME_OPTIONS:
            flash("테마 기본값이 올바르지 않습니다.", "warning")
            return redirect(url_for("settings.formats_index"))

        setting.date_format = next_date_format
        setting.datetime_format = next_datetime_format
        setting.currency_symbol = next_currency_symbol
        setting.number_grouping = _is_truthy(request.form.get("number_grouping"))
        setting.preferred_theme = next_theme
        db.session.commit()

        flash("사용자 서식설정을 저장했습니다.", "success")
        return redirect(url_for("settings.formats_index"))

    sample = datetime(2026, 5, 2, 14, 35)
    sample_number = 1234567.89
    number_preview = f"{sample_number:,.2f}" if setting.number_grouping else f"{sample_number:.2f}"
    currency_preview = (
        f"{number_preview}{setting.currency_symbol}"
        if setting.currency_symbol == "원"
        else f"{setting.currency_symbol} {number_preview}"
    )

    return render_template(
        "settings/formats.html",
        setting=setting,
        sample_date=sample.strftime(DATE_FORMAT_OPTIONS[setting.date_format]),
        sample_datetime=sample.strftime(DATETIME_FORMAT_OPTIONS[setting.datetime_format]),
        sample_currency=currency_preview,
    )


@settings_bp.get("/users")
def users_index():
    _require_admin()
    users = User.query.order_by(User.id.asc()).all()
    admin_count = User.query.filter_by(is_admin=True).count()
    return render_template(
        "settings/users.html",
        users=users,
        admin_count=admin_count,
    )


@settings_bp.post("/users/add")
def user_add():
    _require_admin()

    username = (request.form.get("username") or "").strip()
    display_name = (request.form.get("display_name") or "").strip()
    password = (request.form.get("password") or "").strip()

    if not username:
        flash("아이디를 입력해 주세요.", "warning")
        return redirect(url_for("settings.users_index"))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username):
        flash("아이디는 3~30자, 영문/숫자/._-만 사용할 수 있습니다.", "warning")
        return redirect(url_for("settings.users_index"))
    if User.query.filter_by(username=username).first():
        flash(f"이미 존재하는 아이디입니다: {username}", "danger")
        return redirect(url_for("settings.users_index"))

    password_error = _validate_password_strength(password)
    if password_error:
        flash(password_error, "warning")
        return redirect(url_for("settings.users_index"))

    user = User(
        username=username,
        display_name=display_name or username,
        is_active=_is_truthy(request.form.get("is_active", "on")),
        is_admin=_is_truthy(request.form.get("is_admin")),
    )
    user.set_password(password)

    db.session.add(user)
    db.session.commit()

    flash(f"사용자 [{username}]를 추가했습니다.", "success")
    return redirect(url_for("settings.users_index"))


@settings_bp.post("/users/<int:uid>/edit")
def user_edit(uid: int):
    _require_admin()

    user = db.session.get(User, uid)
    if user is None:
        flash("사용자를 찾지 못했습니다.", "danger")
        return redirect(url_for("settings.users_index"))

    next_display_name = (request.form.get("display_name") or "").strip()
    next_is_admin = _is_truthy(request.form.get("is_admin"))
    next_is_active = _is_truthy(request.form.get("is_active"))
    reset_password = (request.form.get("reset_password") or "").strip()

    # 본인 계정은 관리자/활성 토글을 화면에서 막아두고, 서버에서도 기존 값을 유지한다.
    if user.id == current_user.id:
        next_is_admin = user.is_admin
        next_is_active = user.is_active

    admin_count = User.query.filter_by(is_admin=True).count()
    if user.is_admin and not next_is_admin and admin_count <= 1:
        flash("최소 1명의 관리자 계정은 유지해야 합니다.", "warning")
        return redirect(url_for("settings.users_index"))

    user.display_name = next_display_name or user.username
    user.is_admin = next_is_admin
    user.is_active = next_is_active

    if reset_password:
        password_error = _validate_password_strength(reset_password)
        if password_error:
            flash(password_error, "warning")
            return redirect(url_for("settings.users_index"))
        user.set_password(reset_password)

    db.session.commit()
    flash(f"사용자 [{user.username}] 정보를 저장했습니다.", "success")
    return redirect(url_for("settings.users_index"))


@settings_bp.post("/users/<int:uid>/delete")
def user_delete(uid: int):
    _require_admin()

    user = db.session.get(User, uid)
    if user is None:
        flash("사용자를 찾지 못했습니다.", "danger")
        return redirect(url_for("settings.users_index"))

    if user.id == current_user.id:
        flash("현재 로그인한 본인 계정은 삭제할 수 없습니다.", "warning")
        return redirect(url_for("settings.users_index"))

    admin_count = User.query.filter_by(is_admin=True).count()
    if user.is_admin and admin_count <= 1:
        flash("최소 1명의 관리자 계정은 유지해야 합니다.", "warning")
        return redirect(url_for("settings.users_index"))

    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f"사용자 [{username}]를 삭제했습니다.", "success")
    return redirect(url_for("settings.users_index"))
