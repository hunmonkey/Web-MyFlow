from __future__ import annotations

from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

personal_bp = Blueprint("personal", __name__, url_prefix="/personal")


@personal_bp.before_request
def _require_login():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=request.url))

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


def _to_int(raw_value: str | None, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(str(raw_value or "").strip())
    except (TypeError, ValueError):
        return default

    return max(min_value, min(max_value, parsed))


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


def _coming_soon(message: str = "이 기능은 다음 단계에서 연결할 예정입니다."):
    flash(message, "info")
    return redirect(request.referrer or url_for("personal.index"))


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
    return render_template(
        "personal/index.html",
        monthly_income=0,
        monthly_expense=0,
        recent_transactions=[],
        health_alert={
            "level": "info",
            "title": "복구 페이지 연결이 완료되었습니다",
            "message": "세부 데이터 연결은 순서대로 붙이면 됩니다.",
            "color": "#0d6efd",
        },
    )


@personal_bp.route("/finance")
def finance():
    today = date.today()
    year = _to_int(request.args.get("year"), today.year, 2000, 2100)
    month = _to_int(request.args.get("month"), today.month, 1, 12)
    nav = _month_navigation(year, month)

    return render_template(
        "personal/finance.html",
        year=year,
        month=month,
        today=today,
        can_export_finance=False,
        transactions=[],
        income_total=0,
        expense_total=0,
        expense_categories=_DEFAULT_EXPENSE_CATEGORIES,
        income_categories=_DEFAULT_INCOME_CATEGORIES,
        payment_methods=_DEFAULT_PAYMENT_METHODS,
        expense_by_category={},
        income_by_category={},
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
    return _coming_soon()


@personal_bp.route("/finance/delete/<int:finance_id>", methods=["POST"])
def finance_delete(finance_id: int):
    _ = finance_id
    return _coming_soon()


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
    return render_template(
        "personal/subscriptions.html",
        active_subs=[],
        inactive_subs=[],
        monthly_total=0,
        billing_cycles=_DEFAULT_BILLING_CYCLES,
        payment_methods=_DEFAULT_PAYMENT_METHODS,
        sub_categories=_DEFAULT_SUBSCRIPTION_CATEGORIES,
        today=date.today(),
    )


@personal_bp.route("/subscriptions/add", methods=["POST"])
def subscription_add():
    return _coming_soon()


@personal_bp.route("/subscriptions/edit/<int:sid>", methods=["POST"])
def subscription_edit(sid: int):
    _ = sid
    return _coming_soon()


@personal_bp.route("/subscriptions/toggle/<int:sid>", methods=["POST"])
def subscription_toggle(sid: int):
    _ = sid
    return _coming_soon()


@personal_bp.route("/subscriptions/delete/<int:sid>", methods=["POST"])
def subscription_delete(sid: int):
    _ = sid
    return _coming_soon()


@personal_bp.route("/health")
def health():
    return render_template(
        "personal/health.html",
        latest=None,
        recent_logs=[],
        avg_weight=None,
        avg_glucose=None,
        avg_systolic=None,
        avg_diastolic=None,
        glucose_info={"label": "미입력", "color": "#9ca3af"},
        pressure_info={"label": "미입력", "color": "#9ca3af"},
        weight_change=None,
        meds_rate=0,
        weekly_comment="아직 건강 데이터가 없어 안내 문구만 표시합니다.",
        month_record_count=0,
        month_meds_rate=0,
        month_high_glucose_count=0,
        month_high_pressure_count=0,
        chart_dates=[],
        chart_weight=[],
        chart_glucose=[],
        chart_pressure_systolic=[],
        chart_pressure_diastolic=[],
        logs_by_date={},
        today=date.today(),
    )


@personal_bp.route("/health/add", methods=["POST"])
def health_add():
    return _coming_soon()


@personal_bp.route("/health/delete/<int:lid>", methods=["POST"])
def health_delete(lid: int):
    _ = lid
    return _coming_soon()


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
