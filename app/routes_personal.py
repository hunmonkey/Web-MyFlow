from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import inspect, or_, text

from app.backup_service import (
    BACKUP_TYPE_DATA,
    BACKUP_TYPE_DB,
    WEEKDAY_SHORT_LABELS,
    ensure_default_backup_schedules,
    weekday_mask_to_values,
)
from app.extensions import csrf, db
from app.models import (
    FinanceTransaction,
    HealthLog,
    PersonalShortcut,
    PersonalShortcutGroup,
    PersonalPaletteSet,
    PersonalSchedule,
    PersonalSubscription,
    Profile,
    UserFormatSetting,
)

personal_bp = Blueprint("personal", __name__, url_prefix="/personal")
api_bp = Blueprint("api", __name__, url_prefix="/api")
csrf.exempt(api_bp)


@personal_bp.before_request
def _require_login():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=request.url))
    return None


@api_bp.before_request
def _require_api_login():
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    return None


_DEFAULT_EXPENSE_CATEGORIES = [
    "식비",
    "교통",
    "주거/공과금",
    "의료/건강",
    "문화/여가",
    "쇼핑",
    "교육",
    "통신",
    "자동차",
    "기타",
]
_DEFAULT_INCOME_CATEGORIES = ["급여", "부수입", "환급", "기타"]
_DEFAULT_PAYMENT_METHODS = ["현금", "계좌이체", "카드"]
_DEFAULT_BILLING_CYCLES = [
    ("monthly", "매월"),
    ("yearly", "매년"),
    ("quarterly", "분기"),
]
_DEFAULT_SUBSCRIPTION_CATEGORIES = ["OTT", "음악", "클라우드", "업무", "기타"]
_PALETTE_PRESET_COLORS = [
    "#0F766E",
    "#991B1B",
    "#92400E",
    "#166534",
]
_PALETTE_PAGE_SIZE = 50
_KOREAN_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")
_DISPLAY_WEEKDAY_ORDER = (6, 0, 1, 2, 3, 4, 5)


def _ensure_palette_columns() -> None:
    table_name = PersonalPaletteSet.__tablename__
    try:
        columns = {row["name"] for row in inspect(db.engine).get_columns(table_name)}
    except Exception as exc:  # pragma: no cover
        current_app.logger.exception("팔레트 컬럼 조회 실패: %s", exc)
        return

    if "description" in columns:
        return

    try:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    "ADD COLUMN description VARCHAR(300)"
                )
            )
    except Exception as exc:  # pragma: no cover
        current_app.logger.exception("팔레트 컬럼 추가 실패: %s", exc)
        return


def _ensure_shortcuts_table() -> None:
    shortcut_table_name = PersonalShortcut.__tablename__
    group_table_name = PersonalShortcutGroup.__tablename__
    try:
        table_names = set(inspect(db.engine).get_table_names())
    except Exception as exc:  # pragma: no cover
        current_app.logger.exception("바로가기 테이블 조회 실패: %s", exc)
        return

    if shortcut_table_name not in table_names or group_table_name not in table_names:
        try:
            db.create_all()
        except Exception as exc:  # pragma: no cover
            current_app.logger.exception("바로가기 테이블 생성 실패: %s", exc)
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


def _normalize_shortcut_group_id(raw_value: str | None) -> int | None:
    try:
        parsed = int(str(raw_value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _ensure_default_shortcut_group(user_id: int) -> list[PersonalShortcutGroup]:
    groups = (
        PersonalShortcutGroup.query.filter_by(user_id=user_id)
        .order_by(PersonalShortcutGroup.sort_order.asc(), PersonalShortcutGroup.id.asc())
        .all()
    )
    if groups:
        return groups

    row = PersonalShortcutGroup(user_id=user_id, name="기본", sort_order=1)
    db.session.add(row)
    db.session.commit()
    return [row]


def _normalize_hex(raw_value: str | None) -> str:
    text = str(raw_value or "").strip().lstrip("#").upper()
    if len(text) != 6:
        return ""
    if any(ch not in "0123456789ABCDEF" for ch in text):
        return ""
    return f"#{text}"


def _normalize_rgb(raw_value: str | None) -> str:
    text = (str(raw_value or "").strip().replace("rgb", "", 1).replace("RGB", "", 1)
            .replace(" ", "").replace("(", "").replace(")", ""))
    if not text:
        return ""
    parts = text.split(",") if "," in text else text.split(" ")
    if len(parts) != 3:
        return ""
    values = []
    for part in parts:
        try:
            value = int(part)
        except ValueError:
            return ""
        if value < 0 or value > 255:
            return ""
        values.append(str(value))
    return ", ".join(values)


def _normalize_site_url(raw_value: str | None) -> str:
    text = (raw_value or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    if lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("ftp://"):
        return text
    return f"https://{text}"


def _serialize_palette_rows(items: list[PersonalPaletteSet]) -> list[dict[str, object]]:
    rows = []
    for item in items:
        rows.append(
            {
                "id": item.id,
                "title": item.title or "색상 조합",
                "description": item.description or "",
                "boxes": [
                    {"hex": item.box1_hex or "", "rgb": item.box1_rgb or ""},
                    {"hex": item.box2_hex or "", "rgb": item.box2_rgb or ""},
                    {"hex": item.box3_hex or "", "rgb": item.box3_rgb or ""},
                    {"hex": item.box4_hex or "", "rgb": item.box4_rgb or ""},
                ],
            }
        )
    return rows


def _to_int(raw_value: str | None, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(str(raw_value or "").strip())
    except (TypeError, ValueError):
        return default

    return max(min_value, min(max_value, parsed))


def _to_float(raw_value: str | None) -> float | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value


def _parse_date(raw_value: str | None) -> date | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_time_text(raw_value: str | None) -> str | None:
    text = str(raw_value or "").strip()
    if not text:
        return None

    if len(text) != 5 or text[2] != ":":
        return None

    try:
        hour = int(text[:2])
        minute = int(text[3:])
    except ValueError:
        return None

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_schedule_datetime(raw_value: str | None, all_day: bool) -> datetime | None:
    text = str(raw_value or "").strip()
    if not text:
        return None

    if all_day:
        parsed_date = _parse_date(text[:10])
        if parsed_date is None:
            return None
        return datetime.combine(parsed_date, time.min)

    normalized = text.replace("Z", "+00:00")
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _month_navigation(year: int, month: int) -> dict[str, int]:
    prev_month = 12 if month == 1 else month - 1
    prev_year = year - 1 if month == 1 else year
    next_month = 1 if month == 12 else month + 1
    next_year = year + 1 if month == 12 else year

    return {
        "prev_month": prev_month,
        "prev_year": prev_year,
        "next_month": next_month,
        "next_year": next_year,
    }


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _next_month_start(year: int, month: int) -> date:
    if month == 12:
        return date(year + 1, 1, 1)
    return date(year, month + 1, 1)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    absolute_month = year * 12 + (month - 1) + delta
    return absolute_month // 12, (absolute_month % 12) + 1


def _coming_soon(message: str = "이 기능은 다음 단계에서 연결할 예정입니다."):
    flash(message, "info")
    return redirect(request.referrer or url_for("personal.index"))


def _format_weekday_mask(mask: int) -> str:
    selected_days = set(weekday_mask_to_values(mask))
    if not selected_days:
        return "-"
    if len(selected_days) == 7:
        return "매일"

    labels = [WEEKDAY_SHORT_LABELS[day] for day in _DISPLAY_WEEKDAY_ORDER if day in selected_days]
    return ",".join(labels)


def _format_backup_schedule_text(schedule) -> str:
    if schedule is None or not schedule.is_enabled:
        return "사용 안함"
    day_text = _format_weekday_mask(schedule.weekday)
    return f"{day_text} {schedule.run_time}"


def _get_gallery_summary() -> dict[str, str | int]:
    upload_root = Path(current_app.config["UPLOAD_FOLDER"])
    gallery_root = upload_root / "gallery"
    if not gallery_root.exists():
        return {
            "image_count": 0,
            "category_count": 0,
            "latest_upload_text": "--",
        }

    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
    image_files = [
        item
        for item in gallery_root.iterdir()
        if item.is_file() and item.suffix.lower() in image_extensions
    ]

    if not image_files:
        return {
            "image_count": 0,
            "category_count": 0,
            "latest_upload_text": "--",
        }

    latest_file = max(image_files, key=lambda f: f.stat().st_mtime)
    latest_text = datetime.fromtimestamp(latest_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return {
        "image_count": len(image_files),
        "category_count": 1,
        "latest_upload_text": latest_text,
    }


def _load_month_transactions(user_id: int, year: int, month: int) -> list[FinanceTransaction]:
    start_date = _month_start(year, month)
    next_start = _next_month_start(year, month)
    return (
        FinanceTransaction.query.filter(
            FinanceTransaction.user_id == user_id,
            FinanceTransaction.transaction_date >= start_date,
            FinanceTransaction.transaction_date < next_start,
        )
        .order_by(FinanceTransaction.transaction_date.desc(), FinanceTransaction.id.desc())
        .all()
    )


def _summarize_transactions(transactions: list[FinanceTransaction]) -> dict[str, object]:
    income_total = 0
    expense_total = 0
    income_by_category: dict[str, int] = defaultdict(int)
    expense_by_category: dict[str, int] = defaultdict(int)

    for tx in transactions:
        amount = int(tx.amount or 0)
        category = (tx.category or "기타").strip() or "기타"
        if tx.transaction_type == "income":
            income_total += amount
            income_by_category[category] += amount
        else:
            expense_total += amount
            expense_by_category[category] += amount

    return {
        "income_total": income_total,
        "expense_total": expense_total,
        "income_by_category": dict(income_by_category),
        "expense_by_category": dict(expense_by_category),
    }


def _subscription_monthly_amount(sub: PersonalSubscription) -> float:
    amount = float(sub.billing_amount or 0)
    cycle = (sub.billing_cycle or "monthly").strip().lower()
    if cycle == "yearly":
        return amount / 12.0
    if cycle == "quarterly":
        return amount / 3.0
    return amount


def _glucose_info(value: int | None) -> dict[str, str]:
    if value is None:
        return {"label": "미입력", "color": "#9ca3af"}
    if value >= 126:
        return {"label": "주의", "color": "#ef4444"}
    if value >= 100:
        return {"label": "경계", "color": "#f59e0b"}
    return {"label": "안정", "color": "#22c55e"}


def _pressure_info(systolic: int | None, diastolic: int | None) -> dict[str, str]:
    if systolic is None:
        return {"label": "미입력", "color": "#9ca3af"}
    if systolic >= 140 or (diastolic is not None and diastolic >= 90):
        return {"label": "주의", "color": "#ef4444"}
    if systolic >= 120 or (diastolic is not None and diastolic >= 80):
        return {"label": "관찰", "color": "#f59e0b"}
    return {"label": "안정", "color": "#22c55e"}


def _serialize_schedule(row: PersonalSchedule) -> dict[str, object]:
    if row.all_day:
        start_value = row.start_at.date().isoformat()
        end_value = row.end_at.date().isoformat() if row.end_at else None
    else:
        start_value = row.start_at.isoformat()
        end_value = row.end_at.isoformat() if row.end_at else None

    return {
        "id": row.id,
        "title": row.title,
        "start": start_value,
        "end": end_value,
        "allDay": bool(row.all_day),
        "backgroundColor": row.color,
        "borderColor": row.color,
        "textColor": "#ffffff",
        "extendedProps": {
            "category": row.category,
            "description": row.description or "",
        },
    }


@personal_bp.app_context_processor
def _inject_personal_helpers():
    is_admin_user = bool(getattr(current_user, "is_admin", False))
    return {
        "can_write": lambda _name=None: True,
        "can_read": lambda _name=None: True,
        "_is_admin": is_admin_user,
    }


@personal_bp.get("/")
def index():
    user_id = int(current_user.id)
    today = date.today()
    month_transactions = _load_month_transactions(user_id, today.year, today.month)
    finance_summary_data = _summarize_transactions(month_transactions)

    recent_transactions = (
        FinanceTransaction.query.filter_by(user_id=user_id)
        .order_by(FinanceTransaction.transaction_date.desc(), FinanceTransaction.id.desc())
        .limit(5)
        .all()
    )

    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    week_start_dt = datetime.combine(week_start, time.min)
    week_end_dt = datetime.combine(week_end + timedelta(days=1), time.min)
    week_event_count = (
        PersonalSchedule.query.filter(
            PersonalSchedule.user_id == user_id,
            PersonalSchedule.start_at < week_end_dt,
            or_(PersonalSchedule.end_at.is_(None), PersonalSchedule.end_at >= week_start_dt),
        )
        .count()
    )

    subscriptions_all = PersonalSubscription.query.filter_by(user_id=user_id).all()
    active_subs = [row for row in subscriptions_all if row.is_active]
    inactive_subs = [row for row in subscriptions_all if not row.is_active]
    subscription_monthly_total = int(round(sum(_subscription_monthly_amount(row) for row in active_subs)))

    latest_health = (
        HealthLog.query.filter_by(user_id=user_id)
        .order_by(HealthLog.log_date.desc(), HealthLog.id.desc())
        .first()
    )
    month_log_cutoff = today - timedelta(days=29)
    month_logs = (
        HealthLog.query.filter(
            HealthLog.user_id == user_id,
            HealthLog.log_date >= month_log_cutoff,
            HealthLog.log_date <= today,
        )
        .all()
    )
    month_alert_count = sum(
        1
        for row in month_logs
        if (row.blood_sugar is not None and row.blood_sugar >= 126)
        or (
            row.blood_pressure_systolic is not None
            and (
                row.blood_pressure_systolic >= 140
                or (
                    row.blood_pressure_diastolic is not None
                    and row.blood_pressure_diastolic >= 90
                )
            )
        )
    )

    profile = Profile.query.order_by(Profile.id.asc()).first()
    user_format = UserFormatSetting.query.filter_by(user_id=user_id).first()
    theme_label_map = {
        "system": "시스템 기본",
        "light": "라이트",
        "dark": "다크",
    }
    preferred_theme = theme_label_map.get(
        str(getattr(user_format, "preferred_theme", "system")).strip().lower(),
        "시스템 기본",
    )

    backup_schedules = ensure_default_backup_schedules()
    db_schedule = backup_schedules.get(BACKUP_TYPE_DB)
    data_schedule = backup_schedules.get(BACKUP_TYPE_DATA)
    enabled_backup_count = sum(1 for row in backup_schedules.values() if row.is_enabled)

    gallery_summary = _get_gallery_summary()

    return render_template(
        "personal/index.html",
        monthly_income=finance_summary_data["income_total"],
        monthly_expense=finance_summary_data["expense_total"],
        recent_transactions=recent_transactions,
        finance_summary={
            "balance": finance_summary_data["income_total"] - finance_summary_data["expense_total"],
            "recent_count": len(month_transactions),
        },
        calendar_summary={
            "today_text": f"{today.strftime('%Y-%m-%d')} ({_KOREAN_WEEKDAYS[today.weekday()]})",
            "week_range_text": f"{week_start.strftime('%m/%d')} ~ {week_end.strftime('%m/%d')}",
            "event_count_text": f"{week_event_count}건",
        },
        subscription_summary={
            "active_count": len(active_subs),
            "inactive_count": len(inactive_subs),
            "monthly_total": subscription_monthly_total,
        },
        health_summary={
            "month_record_count": len(month_logs),
            "monthly_alert_count": month_alert_count,
            "last_log_text": latest_health.log_date.strftime("%Y-%m-%d") if latest_health else "--",
        },
        gallery_summary=gallery_summary,
        settings_summary={
            "profile_name": (profile.name or "").strip() if profile else "",
            "profile_headline": (profile.headline or "").strip() if profile else "",
            "preferred_theme": preferred_theme,
            "enabled_backup_count": enabled_backup_count,
            "total_backup_count": len(backup_schedules),
            "db_schedule_text": _format_backup_schedule_text(db_schedule),
            "data_schedule_text": _format_backup_schedule_text(data_schedule),
        },
    )


@personal_bp.route("/finance")
def finance():
    user_id = int(current_user.id)
    today = date.today()
    year = _to_int(request.args.get("year"), today.year, 2000, 2100)
    month = _to_int(request.args.get("month"), today.month, 1, 12)
    nav = _month_navigation(year, month)

    transactions = _load_month_transactions(user_id, year, month)
    summary = _summarize_transactions(transactions)

    return render_template(
        "personal/finance.html",
        year=year,
        month=month,
        today=today,
        can_export_finance=False,
        transactions=transactions,
        income_total=summary["income_total"],
        expense_total=summary["expense_total"],
        expense_categories=_DEFAULT_EXPENSE_CATEGORIES,
        income_categories=_DEFAULT_INCOME_CATEGORIES,
        payment_methods=_DEFAULT_PAYMENT_METHODS,
        expense_by_category=summary["expense_by_category"],
        income_by_category=summary["income_by_category"],
        prev_month=nav["prev_month"],
        prev_year=nav["prev_year"],
        next_month=nav["next_month"],
        next_year=nav["next_year"],
    )


@personal_bp.route("/finance/export-excel")
def finance_export_excel():
    return _coming_soon("엑셀 내보내기는 다음 단계에서 연결하겠습니다.")


@personal_bp.route("/finance/add", methods=["POST"])
def finance_add():
    user_id = int(current_user.id)

    transaction_type = (request.form.get("transaction_type") or "").strip().lower()
    if transaction_type not in {"income", "expense"}:
        flash("거래 구분(수입/지출)을 확인해 주세요.", "warning")
        return redirect(url_for("personal.finance"))

    transaction_date = _parse_date(request.form.get("transaction_date"))
    if transaction_date is None:
        flash("거래 날짜를 올바르게 입력해 주세요.", "warning")
        return redirect(url_for("personal.finance"))

    title = (request.form.get("title") or "").strip()
    if not title:
        flash("항목명을 입력해 주세요.", "warning")
        return redirect(url_for("personal.finance"))

    amount = _to_int(request.form.get("amount"), 0, 0, 10**12)
    if amount <= 0:
        flash("금액은 1원 이상 입력해 주세요.", "warning")
        return redirect(url_for("personal.finance"))

    row = FinanceTransaction(
        user_id=user_id,
        transaction_date=transaction_date,
        transaction_type=transaction_type,
        title=title,
        amount=amount,
        category=(request.form.get("category") or "기타").strip() or "기타",
        payment_method=(request.form.get("payment_method") or "현금").strip() or "현금",
        memo=(request.form.get("memo") or "").strip() or None,
    )
    db.session.add(row)
    db.session.commit()

    flash("거래 내역을 저장했습니다.", "success")
    return redirect(url_for("personal.finance", year=transaction_date.year, month=transaction_date.month))


@personal_bp.route("/finance/delete/<int:finance_id>", methods=["POST"])
def finance_delete(finance_id: int):
    row = FinanceTransaction.query.filter_by(id=finance_id, user_id=int(current_user.id)).first()
    if row is None:
        flash("삭제할 거래 내역을 찾지 못했습니다.", "warning")
        return redirect(url_for("personal.finance"))

    target_year = row.transaction_date.year
    target_month = row.transaction_date.month
    db.session.delete(row)
    db.session.commit()

    flash("거래 내역을 삭제했습니다.", "success")
    return redirect(url_for("personal.finance", year=target_year, month=target_month))


@personal_bp.route("/finance/categories")
def finance_categories():
    return render_template(
        "personal/finance_categories.html",
        expense_cats=[],
        income_cats=[],
    )


@personal_bp.route("/finance/categories/add", methods=["POST"])
def finance_category_add():
    return _coming_soon()


@personal_bp.route("/finance/categories/<int:cid>/edit", methods=["POST"])
def finance_category_edit(cid: int):
    _ = cid
    return _coming_soon()


@personal_bp.route("/finance/categories/<int:cid>/delete", methods=["POST"])
def finance_category_delete(cid: int):
    _ = cid
    return _coming_soon()


@personal_bp.route("/finance/payment-accounts")
def payment_accounts():
    return render_template(
        "personal/payment_accounts.html",
        accounts=[],
    )


@personal_bp.route("/finance/payment-accounts/add", methods=["POST"])
def payment_account_add():
    return _coming_soon()


@personal_bp.route("/finance/payment-accounts/<int:aid>/edit", methods=["POST"])
def payment_account_edit(aid: int):
    _ = aid
    return _coming_soon()


@personal_bp.route("/finance/payment-accounts/<int:aid>/delete", methods=["POST"])
def payment_account_delete(aid: int):
    _ = aid
    return _coming_soon()


@personal_bp.route("/calendar")
def calendar():
    return render_template("personal/calendar.html")


@personal_bp.route("/subscriptions")
def subscriptions():
    today = date.today()
    user_id = int(current_user.id)

    all_rows = (
        PersonalSubscription.query.filter_by(user_id=user_id)
        .order_by(PersonalSubscription.is_active.desc(), PersonalSubscription.next_billing_date.asc(), PersonalSubscription.id.desc())
        .all()
    )

    active_subs = []
    inactive_subs = []
    monthly_total = 0.0
    for row in all_rows:
        if row.next_billing_date:
            row.days_until_expiry = (row.next_billing_date - today).days
        else:
            row.days_until_expiry = None

        if row.is_active:
            active_subs.append(row)
            monthly_total += _subscription_monthly_amount(row)
        else:
            inactive_subs.append(row)

    return render_template(
        "personal/subscriptions.html",
        active_subs=active_subs,
        inactive_subs=inactive_subs,
        monthly_total=int(round(monthly_total)),
        billing_cycles=_DEFAULT_BILLING_CYCLES,
        payment_methods=_DEFAULT_PAYMENT_METHODS,
        sub_categories=_DEFAULT_SUBSCRIPTION_CATEGORIES,
        today=today,
    )


@personal_bp.route("/subscriptions/add", methods=["POST"])
def subscription_add():
    user_id = int(current_user.id)

    service_name = (request.form.get("service_name") or "").strip()
    if not service_name:
        flash("서비스명을 입력해 주세요.", "warning")
        return redirect(url_for("personal.subscriptions"))

    billing_amount = _to_int(request.form.get("billing_amount"), 0, 0, 10**12)
    if billing_amount < 0:
        billing_amount = 0

    billing_cycle = (request.form.get("billing_cycle") or "monthly").strip().lower()
    if billing_cycle not in {"monthly", "yearly", "quarterly"}:
        billing_cycle = "monthly"

    row = PersonalSubscription(
        user_id=user_id,
        service_name=service_name,
        billing_amount=billing_amount,
        billing_cycle=billing_cycle,
        next_billing_date=_parse_date(request.form.get("next_billing_date")),
        payment_method=(request.form.get("payment_method") or "카드").strip() or "카드",
        category=(request.form.get("category") or "기타").strip() or "기타",
        notes=(request.form.get("notes") or "").strip() or None,
        is_active=True,
    )
    db.session.add(row)
    db.session.commit()

    flash("구독 정보를 저장했습니다.", "success")
    return redirect(url_for("personal.subscriptions"))


@personal_bp.route("/subscriptions/edit/<int:sid>", methods=["POST"])
def subscription_edit(sid: int):
    row = PersonalSubscription.query.filter_by(id=sid, user_id=int(current_user.id)).first()
    if row is None:
        flash("수정할 구독 정보를 찾지 못했습니다.", "warning")
        return redirect(url_for("personal.subscriptions"))

    service_name = (request.form.get("service_name") or "").strip()
    if not service_name:
        flash("서비스명을 입력해 주세요.", "warning")
        return redirect(url_for("personal.subscriptions"))

    row.service_name = service_name
    row.billing_amount = _to_int(request.form.get("billing_amount"), 0, 0, 10**12)

    billing_cycle = (request.form.get("billing_cycle") or "monthly").strip().lower()
    if billing_cycle not in {"monthly", "yearly", "quarterly"}:
        billing_cycle = "monthly"
    row.billing_cycle = billing_cycle

    row.next_billing_date = _parse_date(request.form.get("next_billing_date"))
    row.payment_method = (request.form.get("payment_method") or "카드").strip() or "카드"
    row.category = (request.form.get("category") or "기타").strip() or "기타"
    row.notes = (request.form.get("notes") or "").strip() or None

    db.session.commit()
    flash("구독 정보를 수정했습니다.", "success")
    return redirect(url_for("personal.subscriptions"))


@personal_bp.route("/subscriptions/toggle/<int:sid>", methods=["POST"])
def subscription_toggle(sid: int):
    row = PersonalSubscription.query.filter_by(id=sid, user_id=int(current_user.id)).first()
    if row is None:
        flash("대상 구독 정보를 찾지 못했습니다.", "warning")
        return redirect(url_for("personal.subscriptions"))

    row.is_active = not bool(row.is_active)
    db.session.commit()
    flash("구독 사용 상태를 변경했습니다.", "success")
    return redirect(url_for("personal.subscriptions"))


@personal_bp.route("/subscriptions/delete/<int:sid>", methods=["POST"])
def subscription_delete(sid: int):
    row = PersonalSubscription.query.filter_by(id=sid, user_id=int(current_user.id)).first()
    if row is None:
        flash("삭제할 구독 정보를 찾지 못했습니다.", "warning")
        return redirect(url_for("personal.subscriptions"))

    db.session.delete(row)
    db.session.commit()
    flash("구독 정보를 삭제했습니다.", "success")
    return redirect(url_for("personal.subscriptions"))


@personal_bp.route("/health")
def health():
    user_id = int(current_user.id)
    today = date.today()

    recent_logs = (
        HealthLog.query.filter_by(user_id=user_id)
        .order_by(HealthLog.log_date.desc(), HealthLog.id.desc())
        .limit(40)
        .all()
    )
    latest = recent_logs[0] if recent_logs else None

    last7_logs = recent_logs[:7]
    month_cutoff = today - timedelta(days=29)
    month_logs = (
        HealthLog.query.filter(
            HealthLog.user_id == user_id,
            HealthLog.log_date >= month_cutoff,
            HealthLog.log_date <= today,
        )
        .all()
    )

    weight_values = [row.weight for row in last7_logs if row.weight is not None]
    glucose_values = [row.blood_sugar for row in last7_logs if row.blood_sugar is not None]
    systolic_values = [row.blood_pressure_systolic for row in last7_logs if row.blood_pressure_systolic is not None]
    diastolic_values = [row.blood_pressure_diastolic for row in last7_logs if row.blood_pressure_diastolic is not None]

    avg_weight = round(sum(weight_values) / len(weight_values), 1) if weight_values else None
    avg_glucose = round(sum(glucose_values) / len(glucose_values), 1) if glucose_values else None
    avg_systolic = round(sum(systolic_values) / len(systolic_values), 1) if systolic_values else None
    avg_diastolic = round(sum(diastolic_values) / len(diastolic_values), 1) if diastolic_values else None

    weight_change = None
    if len(weight_values) >= 2:
        latest_weight = next((row.weight for row in last7_logs if row.weight is not None), None)
        oldest_weight = next((row.weight for row in reversed(last7_logs) if row.weight is not None), None)
        if latest_weight is not None and oldest_weight is not None:
            weight_change = round(latest_weight - oldest_weight, 1)

    meds_count = sum(1 for row in last7_logs if row.meds_taken)
    meds_rate = int(round((meds_count / len(last7_logs)) * 100)) if last7_logs else 0

    month_record_count = len(month_logs)
    month_meds_count = sum(1 for row in month_logs if row.meds_taken)
    month_meds_rate = int(round((month_meds_count / month_record_count) * 100)) if month_record_count else 0

    month_high_glucose_count = sum(1 for row in month_logs if row.blood_sugar is not None and row.blood_sugar >= 126)
    month_high_pressure_count = sum(
        1
        for row in month_logs
        if row.blood_pressure_systolic is not None
        and (
            row.blood_pressure_systolic >= 140
            or (
                row.blood_pressure_diastolic is not None
                and row.blood_pressure_diastolic >= 90
            )
        )
    )

    if month_record_count == 0:
        weekly_comment = "아직 건강 데이터가 없어 안내 문구만 표시합니다."
    elif month_high_glucose_count == 0 and month_high_pressure_count == 0:
        weekly_comment = "최근 30일 기준 위험 수치가 없어 좋은 흐름입니다."
    else:
        weekly_comment = (
            f"최근 30일에 혈당 주의 {month_high_glucose_count}회, "
            f"혈압 주의 {month_high_pressure_count}회가 기록되었습니다."
        )

    glucose_info = _glucose_info(latest.blood_sugar if latest else None)
    pressure_info = _pressure_info(
        latest.blood_pressure_systolic if latest else None,
        latest.blood_pressure_diastolic if latest else None,
    )

    chart_rows = list(reversed(recent_logs[:30]))
    chart_dates = [row.log_date.strftime("%m/%d") for row in chart_rows]
    chart_weight = [row.weight for row in chart_rows]
    chart_glucose = [row.blood_sugar for row in chart_rows]
    chart_pressure_systolic = [row.blood_pressure_systolic for row in chart_rows]
    chart_pressure_diastolic = [row.blood_pressure_diastolic for row in chart_rows]

    logs_by_date: dict[str, dict[str, object]] = {}
    for row in recent_logs:
        key = row.log_date.isoformat()
        if key in logs_by_date:
            continue
        logs_by_date[key] = {
            "id": row.id,
            "log_time": row.log_time,
            "weight": row.weight,
            "blood_sugar": row.blood_sugar,
            "blood_pressure_systolic": row.blood_pressure_systolic,
            "blood_pressure_diastolic": row.blood_pressure_diastolic,
            "meds_taken": row.meds_taken,
            "meds_name": row.meds_name,
            "memo": row.memo,
        }

    return render_template(
        "personal/health.html",
        latest=latest,
        recent_logs=recent_logs,
        avg_weight=avg_weight,
        avg_glucose=avg_glucose,
        avg_systolic=avg_systolic,
        avg_diastolic=avg_diastolic,
        glucose_info=glucose_info,
        pressure_info=pressure_info,
        weight_change=weight_change,
        meds_rate=meds_rate,
        weekly_comment=weekly_comment,
        month_record_count=month_record_count,
        month_meds_rate=month_meds_rate,
        month_high_glucose_count=month_high_glucose_count,
        month_high_pressure_count=month_high_pressure_count,
        chart_dates=chart_dates,
        chart_weight=chart_weight,
        chart_glucose=chart_glucose,
        chart_pressure_systolic=chart_pressure_systolic,
        chart_pressure_diastolic=chart_pressure_diastolic,
        logs_by_date=logs_by_date,
        today=today,
    )


@personal_bp.route("/health/add", methods=["POST"])
def health_add():
    user_id = int(current_user.id)

    log_date = _parse_date(request.form.get("log_date"))
    if log_date is None:
        flash("기록 날짜를 확인해 주세요.", "warning")
        return redirect(url_for("personal.health"))

    log_time = _parse_time_text(request.form.get("log_time"))
    if request.form.get("log_time") and log_time is None:
        flash("시간 형식이 올바르지 않습니다. 예: 08:30", "warning")
        return redirect(url_for("personal.health"))

    row = HealthLog.query.filter_by(user_id=user_id, log_date=log_date).first()
    is_new = row is None
    if is_new:
        row = HealthLog(user_id=user_id, log_date=log_date)
        db.session.add(row)

    row.log_time = log_time
    row.weight = _to_float(request.form.get("weight"))
    row.blood_sugar = _to_int(request.form.get("blood_sugar"), 0, 0, 1000) or None
    row.blood_pressure_systolic = _to_int(request.form.get("blood_pressure_systolic"), 0, 0, 400) or None
    row.blood_pressure_diastolic = _to_int(request.form.get("blood_pressure_diastolic"), 0, 0, 300) or None
    row.meds_taken = str(request.form.get("meds_taken") or "").strip().lower() in {"1", "true", "yes", "on", "y"}
    row.meds_name = (request.form.get("meds_name") or "").strip() or None
    row.memo = (request.form.get("memo") or "").strip() or None

    db.session.commit()
    flash("건강 기록을 저장했습니다." if is_new else "건강 기록을 수정했습니다.", "success")
    return redirect(url_for("personal.health"))


@personal_bp.route("/health/delete/<int:lid>", methods=["POST"])
def health_delete(lid: int):
    row = HealthLog.query.filter_by(id=lid, user_id=int(current_user.id)).first()
    if row is None:
        flash("삭제할 건강 기록을 찾지 못했습니다.", "warning")
        return redirect(url_for("personal.health"))

    db.session.delete(row)
    db.session.commit()
    flash("건강 기록을 삭제했습니다.", "success")
    return redirect(url_for("personal.health"))


@personal_bp.route("/gallery")
def gallery():
    return render_template(
        "personal/gallery.html",
        active_cat=(request.args.get("cat") or "").strip(),
        categories=[],
        images=[],
    )


@personal_bp.route("/gallery/upload", methods=["POST"])
def gallery_upload():
    return _coming_soon()


@personal_bp.route("/gallery/delete/<int:gid>", methods=["POST"])
def gallery_delete(gid: int):
    _ = gid
    return _coming_soon()


@personal_bp.route("/shortcuts")
def shortcuts():
    _ensure_shortcuts_table()
    user_id = int(current_user.id)
    shortcut_groups = _ensure_default_shortcut_group(user_id)
    shortcut_items = PersonalShortcut.query.filter_by(user_id=user_id).order_by(
        PersonalShortcut.created_at.desc(), PersonalShortcut.id.desc()
    ).all()
    return render_template(
        "personal/shortcuts.html",
        shortcuts=shortcut_items,
        shortcut_groups=shortcut_groups,
    )


@personal_bp.route("/shortcuts/save", methods=["POST"])
def shortcut_save():
    _ensure_shortcuts_table()
    user_id = int(current_user.id)
    shortcut_groups = _ensure_default_shortcut_group(user_id)
    group_ids = {row.id for row in shortcut_groups}

    site_url = _normalize_site_url(request.form.get("site_url"))
    site_name = (request.form.get("site_name") or "").strip()
    selected_group_id = _normalize_shortcut_group_id(request.form.get("group_id"))

    if not site_url:
        flash("사이트 URL을 입력해 주세요.", "warning")
        return redirect(url_for("personal.shortcuts"))

    if not selected_group_id or selected_group_id not in group_ids:
        selected_group_id = shortcut_groups[0].id

    if not site_name:
        site_name = site_url

    db.session.add(
        PersonalShortcut(
            user_id=user_id,
            site_url=site_url,
            site_name=site_name[:160],
            group_id=selected_group_id,
        )
    )
    db.session.commit()
    flash("바로가기를 저장했습니다.", "success")
    return redirect(url_for("personal.shortcuts"))


@personal_bp.route("/shortcuts/delete/<int:shortcut_id>", methods=["POST"])
def shortcut_delete(shortcut_id: int):
    _ensure_shortcuts_table()
    row = PersonalShortcut.query.filter_by(id=shortcut_id, user_id=int(current_user.id)).first()
    if row is None:
        flash("삭제할 바로가기 정보를 찾지 못했습니다.", "warning")
    else:
        db.session.delete(row)
        db.session.commit()
        flash("바로가기를 삭제했습니다.", "success")
    return redirect(url_for("personal.shortcuts"))


@personal_bp.route("/palette")
def palette():
    _ensure_palette_columns()
    user_id = int(current_user.id)
    page = _to_int(request.args.get("page"), default=1, min_value=1, max_value=999999)
    query = (
        PersonalPaletteSet.query.filter_by(user_id=user_id)
        .order_by(PersonalPaletteSet.created_at.desc())
    )
    pagination = query.paginate(page=page, per_page=_PALETTE_PAGE_SIZE, error_out=False)
    return render_template(
        "personal/palette.html",
        preset_colors=_PALETTE_PRESET_COLORS,
        palette_items=_serialize_palette_rows(pagination.items),
        palette_total=pagination.total,
        palette_page=page,
        palette_pages=pagination.pages,
        palette_has_prev=bool(pagination.has_prev),
        palette_has_next=bool(pagination.has_next),
        palette_prev_num=pagination.prev_num,
        palette_next_num=pagination.next_num,
    )


@personal_bp.route("/palette/save", methods=["POST"])
def palette_save():
    _ensure_palette_columns()
    user_id = int(current_user.id)
    title = (request.form.get("palette_title") or "").strip() or "색상 조합"
    description = (request.form.get("palette_description") or "").strip()[:300]

    row = PersonalPaletteSet(
        user_id=user_id,
        title=title[:120],
        description=description or None,
        box1_hex=_normalize_hex(request.form.get("box1_hex")),
        box1_rgb=_normalize_rgb(request.form.get("box1_rgb")),
        box2_hex=_normalize_hex(request.form.get("box2_hex")),
        box2_rgb=_normalize_rgb(request.form.get("box2_rgb")),
        box3_hex=_normalize_hex(request.form.get("box3_hex")),
        box3_rgb=_normalize_rgb(request.form.get("box3_rgb")),
        box4_hex=_normalize_hex(request.form.get("box4_hex")),
        box4_rgb=_normalize_rgb(request.form.get("box4_rgb")),
    )
    db.session.add(row)
    db.session.commit()
    flash("팔레트 조합을 저장했습니다.", "success")
    return redirect(url_for("personal.palette"))


@personal_bp.route("/palette/edit/<int:pid>", methods=["POST"])
def palette_edit(pid: int):
    _ensure_palette_columns()
    user_id = int(current_user.id)
    row = PersonalPaletteSet.query.filter_by(id=pid, user_id=user_id).first()
    if row is None:
        flash("수정할 색상 조합을 찾지 못했습니다.", "warning")
        return redirect(url_for("personal.palette"))

    title = (request.form.get("palette_title") or "").strip() or "색상 조합"
    description = (request.form.get("palette_description") or "").strip()[:300]
    row.title = title[:120]
    row.description = description or None
    row.box1_hex = _normalize_hex(request.form.get("box1_hex"))
    row.box1_rgb = _normalize_rgb(request.form.get("box1_rgb"))
    row.box2_hex = _normalize_hex(request.form.get("box2_hex"))
    row.box2_rgb = _normalize_rgb(request.form.get("box2_rgb"))
    row.box3_hex = _normalize_hex(request.form.get("box3_hex"))
    row.box3_rgb = _normalize_rgb(request.form.get("box3_rgb"))
    row.box4_hex = _normalize_hex(request.form.get("box4_hex"))
    row.box4_rgb = _normalize_rgb(request.form.get("box4_rgb"))
    db.session.commit()
    flash("색상 조합을 수정했습니다.", "success")
    return redirect(url_for("personal.palette"))


@personal_bp.route("/palette/delete/<int:pid>", methods=["POST"])
def palette_delete(pid: int):
    row = PersonalPaletteSet.query.filter_by(id=pid, user_id=int(current_user.id)).first()
    if row is None:
        flash("삭제할 색상 조합을 찾지 못했습니다.", "warning")
    else:
        db.session.delete(row)
        db.session.commit()
        flash("색상 조합을 삭제했습니다.", "success")
    return redirect(url_for("personal.palette"))


@personal_bp.route("/finance/loans")
def loans():
    return render_template(
        "personal/loans.html",
        loans=[],
        total_debt=0,
        total_monthly=0,
        today=date.today(),
    )


@personal_bp.route("/finance/loans/add", methods=["POST"])
def loan_add():
    return _coming_soon()


@personal_bp.route("/finance/loans/<int:lid>/edit", methods=["POST"])
def loan_edit(lid: int):
    _ = lid
    return _coming_soon()


@personal_bp.route("/finance/loans/<int:lid>/delete", methods=["POST"])
def loan_delete(lid: int):
    _ = lid
    return _coming_soon()


@api_bp.get("/schedules")
def schedules_list():
    user_id = int(current_user.id)
    query = PersonalSchedule.query.filter_by(user_id=user_id)

    start_dt = _parse_schedule_datetime(request.args.get("start"), all_day=False)
    end_dt = _parse_schedule_datetime(request.args.get("end"), all_day=False)

    if start_dt is not None:
        query = query.filter(or_(PersonalSchedule.end_at.is_(None), PersonalSchedule.end_at >= start_dt))
    if end_dt is not None:
        query = query.filter(PersonalSchedule.start_at <= end_dt)

    rows = query.order_by(PersonalSchedule.start_at.asc(), PersonalSchedule.id.asc()).all()
    return jsonify([_serialize_schedule(row) for row in rows])


@api_bp.post("/schedules")
def schedules_add():
    payload = request.get_json(silent=True) or {}
    all_day = bool(payload.get("allDay"))

    title = str(payload.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "제목을 입력해 주세요."}), 400

    start_at = _parse_schedule_datetime(payload.get("start"), all_day=all_day)
    if start_at is None:
        return jsonify({"ok": False, "error": "시작 시간을 확인해 주세요."}), 400

    end_at = _parse_schedule_datetime(payload.get("end"), all_day=all_day)
    if end_at is not None and end_at < start_at:
        return jsonify({"ok": False, "error": "종료 시간이 시작 시간보다 빠릅니다."}), 400

    row = PersonalSchedule(
        user_id=int(current_user.id),
        title=title,
        start_at=start_at,
        end_at=end_at,
        all_day=all_day,
        category=str(payload.get("category") or "personal").strip() or "personal",
        color=str(payload.get("color") or "#0d6efd").strip() or "#0d6efd",
        description=str(payload.get("description") or "").strip() or None,
    )
    db.session.add(row)
    db.session.commit()

    return jsonify({"ok": True, "id": row.id})


@api_bp.put("/schedules/<int:sid>")
def schedules_edit(sid: int):
    row = PersonalSchedule.query.filter_by(id=sid, user_id=int(current_user.id)).first()
    if row is None:
        return jsonify({"ok": False, "error": "일정을 찾지 못했습니다."}), 404

    payload = request.get_json(silent=True) or {}

    all_day = row.all_day
    if "allDay" in payload:
        all_day = bool(payload.get("allDay"))

    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            return jsonify({"ok": False, "error": "제목을 입력해 주세요."}), 400
        row.title = title

    if "start" in payload:
        next_start = _parse_schedule_datetime(payload.get("start"), all_day=all_day)
        if next_start is None:
            return jsonify({"ok": False, "error": "시작 시간을 확인해 주세요."}), 400
        row.start_at = next_start

    if "end" in payload:
        end_value = payload.get("end")
        row.end_at = _parse_schedule_datetime(end_value, all_day=all_day) if end_value else None

    row.all_day = all_day

    if "category" in payload:
        row.category = str(payload.get("category") or "personal").strip() or "personal"
    if "color" in payload:
        row.color = str(payload.get("color") or "#0d6efd").strip() or "#0d6efd"
    if "description" in payload:
        row.description = str(payload.get("description") or "").strip() or None

    if row.end_at is not None and row.end_at < row.start_at:
        return jsonify({"ok": False, "error": "종료 시간이 시작 시간보다 빠릅니다."}), 400

    db.session.commit()
    return jsonify({"ok": True})


@api_bp.delete("/schedules/<int:sid>")
def schedules_delete(sid: int):
    row = PersonalSchedule.query.filter_by(id=sid, user_id=int(current_user.id)).first()
    if row is None:
        return jsonify({"ok": False, "error": "일정을 찾지 못했습니다."}), 404

    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.get("/stats/personal")
def personal_stats():
    months = _to_int(request.args.get("months"), 6, 1, 24)
    user_id = int(current_user.id)
    today = date.today()

    month_pairs = []
    for offset in range(months - 1, -1, -1):
        y, m = _shift_month(today.year, today.month, -offset)
        month_pairs.append((y, m))

    oldest_year, oldest_month = month_pairs[0]
    newest_year, newest_month = month_pairs[-1]
    oldest_start = _month_start(oldest_year, oldest_month)
    newest_next_start = _next_month_start(newest_year, newest_month)

    tx_rows = (
        FinanceTransaction.query.filter(
            FinanceTransaction.user_id == user_id,
            FinanceTransaction.transaction_date >= oldest_start,
            FinanceTransaction.transaction_date < newest_next_start,
        )
        .order_by(FinanceTransaction.transaction_date.asc(), FinanceTransaction.id.asc())
        .all()
    )

    monthly_income: dict[tuple[int, int], int] = defaultdict(int)
    monthly_expense: dict[tuple[int, int], int] = defaultdict(int)
    current_expense_by_category: dict[str, int] = defaultdict(int)
    current_income_by_category: dict[str, int] = defaultdict(int)

    for row in tx_rows:
        key = (row.transaction_date.year, row.transaction_date.month)
        if row.transaction_type == "income":
            monthly_income[key] += int(row.amount or 0)
            if key == (today.year, today.month):
                current_income_by_category[(row.category or "기타").strip() or "기타"] += int(row.amount or 0)
        else:
            monthly_expense[key] += int(row.amount or 0)
            if key == (today.year, today.month):
                current_expense_by_category[(row.category or "기타").strip() or "기타"] += int(row.amount or 0)

    labels: list[str] = []
    income_series: list[int] = []
    expense_series: list[int] = []
    balance_series: list[int] = []

    for y, m in month_pairs:
        labels.append(f"{m}월")
        income_value = int(monthly_income.get((y, m), 0))
        expense_value = int(monthly_expense.get((y, m), 0))
        income_series.append(income_value)
        expense_series.append(expense_value)
        balance_series.append(income_value - expense_value)

    current_month_income = income_series[-1] if income_series else 0
    current_month_expense = expense_series[-1] if expense_series else 0
    current_month_balance = current_month_income - current_month_expense

    active_subs = PersonalSubscription.query.filter_by(user_id=user_id, is_active=True).all()
    subscription_monthly_total = int(round(sum(_subscription_monthly_amount(row) for row in active_subs)))

    upcoming_sub_items = []
    for row in active_subs:
        if row.next_billing_date is None:
            continue
        days_left = (row.next_billing_date - today).days
        if 0 <= days_left <= 30:
            upcoming_sub_items.append(
                {
                    "service_name": row.service_name,
                    "next_billing_date": row.next_billing_date.strftime("%Y-%m-%d"),
                    "days_left": days_left,
                }
            )
    upcoming_sub_items.sort(key=lambda item: (item["days_left"], item["service_name"]))

    saving_rate = 0.0
    if current_month_income > 0:
        saving_rate = round((current_month_balance / current_month_income) * 100, 1)

    expense_items = [
        {"category": name, "total": total}
        for name, total in sorted(current_expense_by_category.items(), key=lambda item: item[1], reverse=True)
    ]

    return jsonify(
        {
            "ok": True,
            "scope": {
                "user_name": current_user.display_name or current_user.username,
            },
            "data": {
                "summary": {
                    "current_month_balance": current_month_balance,
                    "saving_rate": saving_rate,
                    "subscription_monthly_total": subscription_monthly_total,
                    "loan_remaining_total": 0,
                },
                "finance_monthly": {
                    "labels": labels,
                    "income": income_series,
                    "expense": expense_series,
                    "balance": balance_series,
                },
                "category_monthly": {
                    "expense_items": expense_items,
                },
                "subscription": {
                    "upcoming_items": upcoming_sub_items,
                },
                "loan": {
                    "upcoming_items": [],
                },
                "expense_by_category": dict(current_expense_by_category),
                "income_by_category": dict(current_income_by_category),
            },
        }
    )
