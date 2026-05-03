from __future__ import annotations

import re
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import inspect, text

from app.backup_service import (
    BACKUP_TYPE_DATA,
    BACKUP_TYPE_DB,
    BACKUP_TYPE_LABELS,
    BACKUP_TYPES,
    WEEKDAY_OPTIONS,
    ensure_default_backup_schedules,
    normalize_backup_type,
    normalize_weekday_mask,
    run_backup,
    validate_run_time,
    weekday_mask_from_values,
    weekday_mask_to_values,
)
from app.extensions import db
from app.models import PersonalShortcut, PersonalShortcutGroup, Profile, User, UserFormatSetting

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


def _get_or_create_user_format_setting(user_id: int) -> UserFormatSetting:
    setting = UserFormatSetting.query.filter_by(user_id=user_id).first()
    if setting is None:
        setting = UserFormatSetting(user_id=user_id)
        db.session.add(setting)
        db.session.commit()
    return setting


def _ensure_shortcut_group_schema() -> None:
    shortcut_table_name = PersonalShortcut.__tablename__
    group_table_name = PersonalShortcutGroup.__tablename__
    try:
        table_names = set(inspect(db.engine).get_table_names())
    except Exception as exc:  # pragma: no cover
        current_app.logger.exception("바로가기 그룹 테이블 조회 실패: %s", exc)
        return

    if shortcut_table_name not in table_names or group_table_name not in table_names:
        try:
            db.create_all()
        except Exception as exc:  # pragma: no cover
            current_app.logger.exception("바로가기 그룹 테이블 생성 실패: %s", exc)
            return

    try:
        columns = {row["name"] for row in inspect(db.engine).get_columns(shortcut_table_name)}
    except Exception as exc:  # pragma: no cover
        current_app.logger.exception("바로가기 컬럼 조회 실패: %s", exc)
        return

    if "group_id" in columns:
        return

    try:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    f"ALTER TABLE {shortcut_table_name} "
                    "ADD COLUMN group_id INTEGER"
                )
            )
    except Exception as exc:  # pragma: no cover
        current_app.logger.exception("바로가기 group_id 컬럼 추가 실패: %s", exc)
        return


def _load_shortcut_groups(user_id: int) -> list[PersonalShortcutGroup]:
    groups = (
        PersonalShortcutGroup.query.filter_by(user_id=user_id)
        .order_by(PersonalShortcutGroup.sort_order.asc(), PersonalShortcutGroup.id.asc())
        .all()
    )
    if groups:
        return groups

    default_group = PersonalShortcutGroup(user_id=user_id, name="기본", sort_order=1)
    db.session.add(default_group)
    db.session.commit()
    return [default_group]


@settings_bp.get("/")
def index():
    profile = Profile.query.order_by(Profile.id.asc()).first()
    backup_schedules = ensure_default_backup_schedules()
    selected_weekdays = {
        backup_type: set(weekday_mask_to_values(row.weekday))
        for backup_type, row in backup_schedules.items()
    }
    return render_template(
        "settings/index.html",
        profile=profile,
        backup_schedules=backup_schedules,
        backup_selected_weekdays=selected_weekdays,
        backup_schedule_order=[BACKUP_TYPE_DB, BACKUP_TYPE_DATA],
        backup_type_labels=BACKUP_TYPE_LABELS,
        weekday_options=WEEKDAY_OPTIONS,
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

    profile.name = (request.form.get("name") or "").strip()
    profile.headline = (request.form.get("headline") or "").strip()
    profile.intro = (request.form.get("intro") or "").strip()
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
    try:
        backup_file = run_backup(BACKUP_TYPE_DB)
        flash(f"DB 백업이 완료되었습니다: {backup_file}", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"DB 백업에 실패했습니다: {exc}", "danger")
    return redirect(url_for("settings.index"))


@settings_bp.post("/backup/data")
def backup_data():
    _require_admin()
    try:
        backup_file = run_backup(BACKUP_TYPE_DATA)
        flash(f"일반자료 백업이 완료되었습니다: {backup_file}", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"일반자료 백업에 실패했습니다: {exc}", "danger")
    return redirect(url_for("settings.index"))


@settings_bp.post("/backup/schedule")
def backup_schedule_update():
    _require_admin()

    backup_type = normalize_backup_type(request.form.get("backup_type"))
    if backup_type not in BACKUP_TYPES:
        flash("백업 종류가 올바르지 않습니다.", "warning")
        return redirect(url_for("settings.index"))

    selected_days: list[int] = []
    for raw_value in request.form.getlist("weekdays"):
        try:
            day_value = int(str(raw_value).strip())
        except ValueError:
            continue
        if 0 <= day_value <= 6:
            selected_days.append(day_value)

    run_time = (request.form.get("run_time") or "").strip()
    if not validate_run_time(run_time):
        flash("시간 형식이 올바르지 않습니다. 예: 03:30", "warning")
        return redirect(url_for("settings.index"))

    usage_action = (request.form.get("usage_action") or "").strip().lower()
    if usage_action not in {"enable", "disable"}:
        flash("사용 설정 값이 올바르지 않습니다.", "warning")
        return redirect(url_for("settings.index"))

    schedules = ensure_default_backup_schedules()
    row = schedules[backup_type]

    weekday_mask = weekday_mask_from_values(selected_days)
    if weekday_mask == 0:
        if usage_action == "enable":
            flash("요일을 1개 이상 선택해 주세요.", "warning")
            return redirect(url_for("settings.index"))
        weekday_mask = normalize_weekday_mask(row.weekday)

    row.is_enabled = usage_action == "enable"
    row.weekday = weekday_mask
    row.run_time = run_time
    db.session.commit()

    status_text = "사용" if row.is_enabled else "사용 안함"
    flash(f"{BACKUP_TYPE_LABELS[backup_type]} 자동 백업 일정을 저장했습니다. ({status_text})", "success")
    return redirect(url_for("settings.index"))


@settings_bp.route("/formats", methods=["GET", "POST"])
def formats_index():
    _ensure_shortcut_group_schema()
    user_id = int(current_user.id)
    setting = _get_or_create_user_format_setting(int(current_user.id))
    shortcut_groups = _load_shortcut_groups(user_id)

    if request.method == "POST":
        next_date_format = (request.form.get("date_format") or "").strip()
        next_datetime_format = (request.form.get("datetime_format") or "").strip()
        next_currency_symbol = (request.form.get("currency_symbol") or "").strip()

        if next_date_format not in DATE_FORMAT_OPTIONS:
            flash("날짜 표시 형식이 올바르지 않습니다.", "warning")
            return redirect(url_for("settings.formats_index"))
        if next_datetime_format not in DATETIME_FORMAT_OPTIONS:
            flash("날짜+시간 표시 형식이 올바르지 않습니다.", "warning")
            return redirect(url_for("settings.formats_index"))
        if next_currency_symbol not in CURRENCY_SYMBOL_OPTIONS:
            flash("통화 표시 형식이 올바르지 않습니다.", "warning")
            return redirect(url_for("settings.formats_index"))

        setting.date_format = next_date_format
        setting.datetime_format = next_datetime_format
        setting.currency_symbol = next_currency_symbol
        setting.number_grouping = _is_truthy(request.form.get("number_grouping"))
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
        shortcut_groups=shortcut_groups,
    )


@settings_bp.post("/formats/shortcut-groups/add")
def formats_shortcut_group_add():
    _ensure_shortcut_group_schema()
    user_id = int(current_user.id)
    group_name = (request.form.get("group_name") or "").strip()
    if not group_name:
        flash("그룹 이름을 입력해 주세요.", "warning")
        return redirect(url_for("settings.formats_index"))

    groups = _load_shortcut_groups(user_id)
    next_sort_order = max((row.sort_order or 0) for row in groups) + 1
    db.session.add(
        PersonalShortcutGroup(
            user_id=user_id,
            name=group_name[:80],
            sort_order=next_sort_order,
        )
    )
    db.session.commit()
    flash("바로가기 그룹을 추가했습니다.", "success")
    return redirect(url_for("settings.formats_index"))


@settings_bp.post("/formats/shortcut-groups/<int:gid>/edit")
def formats_shortcut_group_edit(gid: int):
    _ensure_shortcut_group_schema()
    user_id = int(current_user.id)
    row = PersonalShortcutGroup.query.filter_by(id=gid, user_id=user_id).first()
    if row is None:
        flash("수정할 그룹을 찾지 못했습니다.", "warning")
        return redirect(url_for("settings.formats_index"))

    next_name = (request.form.get("group_name") or "").strip()
    if not next_name:
        flash("그룹 이름을 입력해 주세요.", "warning")
        return redirect(url_for("settings.formats_index"))

    row.name = next_name[:80]
    db.session.commit()
    flash("그룹 이름을 수정했습니다.", "success")
    return redirect(url_for("settings.formats_index"))


@settings_bp.post("/formats/shortcut-groups/<int:gid>/delete")
def formats_shortcut_group_delete(gid: int):
    _ensure_shortcut_group_schema()
    user_id = int(current_user.id)
    groups = _load_shortcut_groups(user_id)
    if len(groups) <= 1:
        flash("그룹은 최소 1개 이상 있어야 합니다.", "warning")
        return redirect(url_for("settings.formats_index"))

    row = PersonalShortcutGroup.query.filter_by(id=gid, user_id=user_id).first()
    if row is None:
        flash("삭제할 그룹을 찾지 못했습니다.", "warning")
        return redirect(url_for("settings.formats_index"))

    replacement = next((item for item in groups if item.id != row.id), None)
    if replacement is None:
        flash("대체 그룹을 찾지 못했습니다.", "warning")
        return redirect(url_for("settings.formats_index"))

    PersonalShortcut.query.filter_by(user_id=user_id, group_id=row.id).update(
        {PersonalShortcut.group_id: replacement.id},
        synchronize_session=False,
    )
    db.session.delete(row)
    db.session.flush()

    remained = (
        PersonalShortcutGroup.query.filter_by(user_id=user_id)
        .order_by(PersonalShortcutGroup.sort_order.asc(), PersonalShortcutGroup.id.asc())
        .all()
    )
    for idx, group in enumerate(remained, start=1):
        group.sort_order = idx

    db.session.commit()
    flash("그룹을 삭제했습니다.", "success")
    return redirect(url_for("settings.formats_index"))


@settings_bp.post("/formats/shortcut-groups/reorder")
def formats_shortcut_group_reorder():
    _ensure_shortcut_group_schema()
    user_id = int(current_user.id)
    groups = _load_shortcut_groups(user_id)
    group_ids = [row.id for row in groups]

    raw_order = (request.form.get("ordered_ids") or "").strip()
    parsed_ids: list[int] = []
    for token in raw_order.split(","):
        token_text = token.strip()
        if not token_text:
            continue
        try:
            token_id = int(token_text)
        except ValueError:
            continue
        if token_id in parsed_ids:
            continue
        parsed_ids.append(token_id)

    if not parsed_ids or set(parsed_ids) != set(group_ids):
        flash("순서 데이터가 올바르지 않아 저장하지 못했습니다.", "warning")
        return redirect(url_for("settings.formats_index"))

    group_map = {row.id: row for row in groups}
    for sort_order, group_id in enumerate(parsed_ids, start=1):
        group_map[group_id].sort_order = sort_order

    db.session.commit()
    flash("그룹 순서를 저장했습니다.", "success")
    return redirect(url_for("settings.formats_index"))


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
