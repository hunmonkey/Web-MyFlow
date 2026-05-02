"""
routes/personal.py - 개인 모듈 라우트 (Phase 2 완성)
포함: 개인 홈, 가계부(CRUD), 캘린더, 구독관리(CRUD), 건강일지(CRUD), 갤러리(업로드)
"""

from pathlib import Path
from flask import (
    abort,
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
)
from flask_login import login_required, current_user
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from sqlalchemy import func, or_
from app.extensions import db
from app.models.personal import (
    Finance,
    Subscription,
    HealthLog,
    GalleryImage,
    FinanceCategory,
    PaymentAccount,
    Loan,
)
from app.models.unified import SystemCode
from app.utils.permissions import ACCESS_WRITE, resolve_permission_for_endpoint, resolve_required_access_level
from app.utils.uploads import ensure_unique_filename, save_uploaded_file
from app.utils.audit import log_audit_event
from app.utils.export import export_to_excel
from app.utils.error_handling import flash_route_error

personal_bp = Blueprint("personal", __name__)


@personal_bp.before_request
@login_required
def _check_personal_permission():
    """개인 블루프린트 전역 권한 검사"""
    if current_user.role == "admin":
        return
    permission = resolve_permission_for_endpoint(request.endpoint) or "personal"
    required_level = resolve_required_access_level(request.endpoint, request.method)
    action = "edit" if required_level == ACCESS_WRITE else "view"
    if not current_user.has_permission(permission, action=action):
        abort(403)


# ── 기본 상수 (DB에 데이터 없을 때 fallback) ─────────────────────────
_DEFAULT_EXPENSE_CATS = [
    "식비",
    "교통",
    "주거/공과금",
    "의료/건강",
    "문화/여가",
    "쇼핑",
    "교육",
    "통신",
    "자동차",
    "경조사",
    "기타",
]
_DEFAULT_INCOME_CATS = ["급여", "용돈", "부업/프리랜서", "투자/배당", "환급", "기타"]
_DEFAULT_PAYMENT_METHODS = ["현금", "계좌이체", "기타"]

# ── 하위 호환용 (구독 관리에서 사용) ─────────────────────────────────
EXPENSE_CATEGORIES = _DEFAULT_EXPENSE_CATS
INCOME_CATEGORIES = _DEFAULT_INCOME_CATS
PAYMENT_METHODS = _DEFAULT_PAYMENT_METHODS
BILLING_CYCLES = [("monthly", "매월"), ("yearly", "매년"), ("quarterly", "분기")]


def _current_user_id() -> int:
    """현재 로그인 사용자의 ID를 정수로 반환한다."""
    return int(current_user.id)


def _owned_query(model):
    """현재 사용자 소유 데이터로만 좁힌 기본 쿼리를 반환한다."""
    return model.query.filter(model.user_id == _current_user_id())


def _정리된_숫자문자(raw_value: str | None) -> str:
    """쉼표와 공백을 걷어낸 숫자 문자열을 돌려준다."""
    return (raw_value or "").replace(",", "").strip()


def _숫자입력_해석(raw_value: str | None) -> tuple[Decimal | None, bool]:
    """숫자 입력을 Decimal로 바꾸고, 실제 입력 여부도 함께 돌려준다."""
    cleaned = _정리된_숫자문자(raw_value)
    if not cleaned:
        return None, False
    try:
        return Decimal(cleaned), True
    except (InvalidOperation, ValueError):
        return None, True


def _날짜입력_해석(raw_value: str | None):
    """날짜 문자열을 date로 바꾸고, 실제 입력 여부도 함께 돌려준다."""
    cleaned = (raw_value or "").strip()
    if not cleaned:
        return None, False
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date(), True
    except ValueError:
        return None, True


def _classify_glucose_level(blood_sugar):
    """혈당 수치를 읽기 쉬운 단계로 바꾼다."""
    if blood_sugar is None:
        return {"label": "미입력", "color": "rgba(255,255,255,.45)"}
    if blood_sugar >= 126:
        return {"label": "주의", "color": "#ff453a"}
    if blood_sugar >= 100:
        return {"label": "경계", "color": "#ffd60a"}
    return {"label": "안정", "color": "#30d158"}


def _classify_pressure_level(systolic, diastolic):
    """혈압 수치를 읽기 쉬운 단계로 바꾼다."""
    if systolic is None:
        return {"label": "미입력", "color": "rgba(255,255,255,.45)"}
    if systolic >= 140 or (diastolic is not None and diastolic >= 90):
        return {"label": "주의", "color": "#ff9f0a"}
    if systolic >= 120 or (diastolic is not None and diastolic >= 80):
        return {"label": "관찰", "color": "#ffd60a"}
    return {"label": "안정", "color": "#30d158"}


def _build_health_weekly_comment(weight_change, avg_glucose, avg_systolic, avg_diastolic, meds_rate):
    """최근 기록을 짧은 한글 코멘트로 요약한다."""
    comments = []

    if weight_change is None:
        comments.append("체중 변화는 아직 비교할 기록이 부족합니다.")
    elif weight_change > 0.7:
        comments.append("최근 체중이 빠르게 올라 조금 천천히 흐름을 살펴보는 편이 좋겠습니다.")
    elif weight_change < -0.7:
        comments.append("최근 체중이 눈에 띄게 내려가고 있어 식사량이나 컨디션을 함께 보는 것이 좋겠습니다.")
    else:
        comments.append("체중 흐름은 비교적 안정적으로 보입니다.")

    if avg_glucose is None:
        comments.append("혈당 평균은 아직 계산할 기록이 부족합니다.")
    elif avg_glucose >= 126:
        comments.append("최근 혈당 평균이 주의 구간이라 식사 시간이나 복약 여부를 함께 점검해 보시면 좋겠습니다.")
    elif avg_glucose >= 100:
        comments.append("최근 혈당 평균이 경계 구간이라 간식이나 야식 패턴을 같이 보는 편이 좋겠습니다.")
    else:
        comments.append("최근 혈당 평균은 안정 구간에 가깝습니다.")

    if avg_systolic is not None:
        if avg_systolic >= 140 or (avg_diastolic is not None and avg_diastolic >= 90):
            comments.append("혈압 평균도 높게 잡혀 있어 휴식 시간과 수면 패턴을 함께 챙기는 편이 좋겠습니다.")
        elif avg_systolic >= 120 or (avg_diastolic is not None and avg_diastolic >= 80):
            comments.append("혈압은 완전 안정권보다는 조금 더 지켜보는 구간입니다.")
        else:
            comments.append("혈압 흐름은 비교적 차분합니다.")

    if meds_rate >= 80:
        comments.append("복약 체크는 꾸준하게 잘 이어지고 있습니다.")
    elif meds_rate >= 1:
        comments.append("복약 기록은 이어지고 있지만 빠진 날이 있어 한 번 더 챙기면 좋겠습니다.")
    else:
        comments.append("복약 체크 기록이 거의 없어 체크만 해도 관리가 훨씬 쉬워집니다.")

    return " ".join(comments[:4])


def _build_health_alert_summary(latest, avg_glucose, avg_systolic, _avg_diastolic):
    """개인 홈에서 바로 보여줄 건강 경고 요약을 만든다."""
    if not latest:
        return {
            "level": "info",
            "title": "건강 기록이 아직 없습니다",
            "message": "첫 기록을 남기면 최근 흐름을 바로 읽을 수 있습니다.",
            "color": "#64d2ff",
        }

    glucose_info = _classify_glucose_level(latest.blood_sugar)
    pressure_info = _classify_pressure_level(
        latest.blood_pressure_systolic, latest.blood_pressure_diastolic
    )

    if glucose_info["label"] == "주의" or pressure_info["label"] == "주의":
        return {
            "level": "danger",
            "title": "최근 건강 수치에 주의가 필요합니다",
            "message": "혈당이나 혈압 중 하나가 높은 편으로 기록되어 최근 흐름을 다시 보는 편이 좋겠습니다.",
            "color": "#ff453a",
        }

    if glucose_info["label"] == "경계" or pressure_info["label"] == "관찰":
        return {
            "level": "warning",
            "title": "최근 건강 수치를 조금 더 지켜보면 좋겠습니다",
            "message": "큰 경고는 아니지만 식사, 수면, 복약 흐름을 함께 보면 도움이 됩니다.",
            "color": "#ffd60a",
        }

    if avg_glucose or avg_systolic:
        return {
            "level": "good",
            "title": "최근 건강 흐름은 비교적 안정적입니다",
            "message": "기록만 꾸준히 이어가면 다음 변화도 바로 파악하기 쉽습니다.",
            "color": "#30d158",
        }

    return {
        "level": "info",
        "title": "건강 기록을 더 쌓아보면 좋겠습니다",
        "message": "아직은 판단할 기록이 적어 조금만 더 적으면 흐름이 또렷해집니다.",
        "color": "#64d2ff",
    }


def _get_expense_categories():
    """DB에서 지출 카테고리 목록 반환. 없으면 기본값 사용."""
    try:
        user_id = _current_user_id()
        rows = (
            FinanceCategory.query.filter(
                FinanceCategory.transaction_type == "expense",
                or_(
                    FinanceCategory.user_id == user_id,
                    FinanceCategory.user_id.is_(None),
                ),
            )
            .order_by(FinanceCategory.sort_order, FinanceCategory.name)
            .all()
        )
        return [r.name for r in rows] if rows else _DEFAULT_EXPENSE_CATS
    except Exception:
        return _DEFAULT_EXPENSE_CATS


def _get_income_categories():
    """DB에서 수입 카테고리 목록 반환. 없으면 기본값 사용."""
    try:
        user_id = _current_user_id()
        rows = (
            FinanceCategory.query.filter(
                FinanceCategory.transaction_type == "income",
                or_(
                    FinanceCategory.user_id == user_id,
                    FinanceCategory.user_id.is_(None),
                ),
            )
            .order_by(FinanceCategory.sort_order, FinanceCategory.name)
            .all()
        )
        return [r.name for r in rows] if rows else _DEFAULT_INCOME_CATS
    except Exception:
        return _DEFAULT_INCOME_CATS


def _get_payment_methods():
    """DB에서 등록된 통장/카드 목록 + 기본 방법 반환."""
    try:
        user_id = _current_user_id()
        accounts = (
            PaymentAccount.query.filter(
                PaymentAccount.user_id == user_id,
                PaymentAccount.is_active.is_(True),
            )
            .order_by(PaymentAccount.sort_order, PaymentAccount.account_name)
            .all()
        )
        dynamic = [a.display_name for a in accounts]
        return dynamic + _DEFAULT_PAYMENT_METHODS if dynamic else _DEFAULT_PAYMENT_METHODS
    except Exception:
        return _DEFAULT_PAYMENT_METHODS


_DEFAULT_SUB_CATEGORIES = [
    "OTT/스트리밍",
    "음악",
    "클라우드/스토리지",
    "업무/생산성",
    "게임",
    "뉴스/미디어",
    "건강/피트니스",
    "기타",
]


def _get_sub_categories() -> list[str]:
    """DB에서 구독 서비스 분류 목록 반환. 없으면 기본값 사용."""
    try:
        names = SystemCode.get_names("SUBSCRIPTION_CATEGORY")
        return names if names else _DEFAULT_SUB_CATEGORIES
    except Exception:
        return _DEFAULT_SUB_CATEGORIES


ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
# 이미지 처리 헬퍼
def _allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXT


def _gallery_upload_base() -> Path:
    """
    갤러리 업로드 기본 경로를 반환한다.
    - UPLOAD_FOLDER를 단일 기준으로 사용한다.
    - 컨테이너 교체 후에도 유지되는 서버 볼륨 경로를 따른다.
    """
    configured_base = Path(current_app.config["UPLOAD_FOLDER"])
    configured_base.mkdir(parents=True, exist_ok=True)
    return configured_base


def _process_image(file_obj) -> dict:
    """
    업로드된 이미지를 원본 형식 그대로 저장하고 썸네일을 생성한다.

    비유: 원본 서류는 그대로 보관하고, 열람용 복사본(썸네일)만 따로 만드는 방식.

    Returns:
        {original_filename, stored_filename, thumbnail_filename, width, height, file_size}
    """
    from PIL import Image, ImageOps

    upload_base = _gallery_upload_base()
    gallery_dir = upload_base / "gallery"
    thumb_dir = upload_base / "thumbnails"
    gallery_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    stored_filename, stored_path = save_uploaded_file(
        file_obj,
        gallery_dir,
        allowed_extensions=ALLOWED_IMAGE_EXT,
        max_length=255,
        extension_error_message="지원하지 않는 이미지 형식입니다.",
        fallback_stem="갤러리_이미지",
    )

    thumbnail_filename = ensure_unique_filename(
        thumb_dir,
        f"{Path(stored_filename).stem}_thumb.webp",
        max_length=255,
    )

    with Image.open(stored_path) as original_img:
        img = ImageOps.exif_transpose(original_img)
        orig_w, orig_h = img.size

        thumb = img.copy()
        thumb.thumbnail((1280, 1280), Image.LANCZOS)
        if thumb.mode not in ("RGB", "RGBA"):
            thumb = thumb.convert("RGB")
        if thumb.mode == "RGBA":
            background = Image.new("RGB", thumb.size, (17, 23, 38))
            background.paste(thumb, mask=thumb.split()[-1])
            thumb = background
        thumb.save(str(thumb_dir / thumbnail_filename), "WEBP", quality=92, method=6, optimize=True)

    try:
        file_size = stored_path.stat().st_size
    except (FileNotFoundError, PermissionError, OSError) as e:
        current_app.logger.warning("업로드 파일 크기 확인 실패(%s): %s", stored_path, e)
        file_size = 0

    return {
        "original_filename": stored_filename,
        "stored_filename": stored_filename,
        "thumbnail_filename": thumbnail_filename,
        "width": orig_w,
        "height": orig_h,
        "file_size": file_size,
    }
# 개인 모듈 홈
@personal_bp.route("/")
@login_required
def index():
    user_id = _current_user_id()
    now_utc = datetime.now(timezone.utc)
    now = now_utc.astimezone()
    ms = date(now.year, now.month, 1)
    nm = date(now.year, now.month + 1, 1) if now.month < 12 else date(now.year + 1, 1, 1)

    income = float(
        db.session.query(func.sum(Finance.amount))
        .filter(
            Finance.user_id == user_id,
            Finance.transaction_type == "income",
            Finance.transaction_date >= ms,
            Finance.transaction_date < nm,
        )
        .scalar()
        or 0
    )
    expense = float(
        db.session.query(func.sum(Finance.amount))
        .filter(
            Finance.user_id == user_id,
            Finance.transaction_type == "expense",
            Finance.transaction_date >= ms,
            Finance.transaction_date < nm,
        )
        .scalar()
        or 0
    )

    recent_health_logs = (
        _owned_query(HealthLog).order_by(HealthLog.log_date.desc(), HealthLog.id.desc()).limit(7).all()
    )
    latest_health = recent_health_logs[0] if recent_health_logs else None
    avg_health_glucose = None
    avg_health_systolic = None
    avg_health_diastolic = None
    if recent_health_logs:
        glucose_values = [log.blood_sugar for log in recent_health_logs if log.blood_sugar is not None]
        systolic_values = [
            log.blood_pressure_systolic
            for log in recent_health_logs
            if log.blood_pressure_systolic is not None
        ]
        diastolic_values = [
            log.blood_pressure_diastolic
            for log in recent_health_logs
            if log.blood_pressure_diastolic is not None
        ]
        if glucose_values:
            avg_health_glucose = round(sum(glucose_values) / len(glucose_values))
        if systolic_values:
            avg_health_systolic = round(sum(systolic_values) / len(systolic_values))
        if diastolic_values:
            avg_health_diastolic = round(sum(diastolic_values) / len(diastolic_values))

    health_alert = _build_health_alert_summary(
        latest_health, avg_health_glucose, avg_health_systolic, avg_health_diastolic
    )

    return render_template(
        "personal/index.html",
        monthly_income=income,
        monthly_expense=expense,
        recent_transactions=_owned_query(Finance).order_by(
            Finance.transaction_date.desc(), Finance.id.desc()
        )
        .limit(5)
        .all(),
        active_subscriptions=_owned_query(Subscription).filter_by(is_active=True).count(),
        health_alert=health_alert,
        latest_health=latest_health,
        now=now,
    )
# 가계부 (Finance)
@personal_bp.route("/finance")
@login_required
def finance():
    user_id = _current_user_id()
    now_utc = datetime.now(timezone.utc)
    now = now_utc.astimezone()
    year = request.args.get("year", now.year, type=int)
    month = request.args.get("month", now.month, type=int)
    if month < 1:
        year -= 1
        month = 12
    if month > 12:
        year += 1
        month = 1

    ms = date(year, month, 1)
    nm = date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)

    txs = (
        Finance.query.filter(
            Finance.user_id == user_id,
            Finance.transaction_date >= ms,
            Finance.transaction_date < nm,
        )
        .order_by(Finance.transaction_date.desc(), Finance.id.desc())
        .all()
    )

    inc_total = sum(float(t.amount) for t in txs if t.transaction_type == "income")
    exp_total = sum(float(t.amount) for t in txs if t.transaction_type == "expense")
    exp_cat = defaultdict(float)
    inc_cat = defaultdict(float)
    for t in txs:
        cat = t.category or "기타"
        (exp_cat if t.transaction_type == "expense" else inc_cat)[cat] += float(t.amount)

    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    ny, nm2 = (year + 1, 1) if month == 12 else (year, month + 1)

    return render_template(
        "personal/finance.html",
        transactions=txs,
        year=year,
        month=month,
        income_total=inc_total,
        expense_total=exp_total,
        expense_by_category=dict(exp_cat),
        income_by_category=dict(inc_cat),
        expense_categories=_get_expense_categories(),
        income_categories=_get_income_categories(),
        payment_methods=_get_payment_methods(),
        prev_year=py,
        prev_month=pm,
        next_year=ny,
        next_month=nm2,
        today=now.date(),
        can_export_finance=(
            current_user.role == "admin"
            or current_user.has_permission("personal.finance", action="export")
        ),
    )


@personal_bp.route("/finance/export-excel")
@login_required
def finance_export_excel():
    """가계부 월 데이터를 엑셀로 내보낸다."""
    if current_user.role != "admin" and not current_user.has_permission(
        "personal.finance", action="export"
    ):
        abort(403)

    user_id = _current_user_id()
    now = datetime.now(timezone.utc).astimezone()
    year = request.args.get("year", now.year, type=int)
    month = request.args.get("month", now.month, type=int)
    if month < 1:
        year -= 1
        month = 12
    if month > 12:
        year += 1
        month = 1

    month_start = date(year, month, 1)
    next_month_start = date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)
    export_filters = {
        "year": year,
        "month": month,
        "user_id": user_id,
    }

    log_audit_event(
        action="EXPORT_ATTEMPT",
        table_name="finances",
        new_values={
            "format": "excel",
            "filters": export_filters,
        },
        user_id=user_id,
    )

    try:
        txs = (
            Finance.query.filter(
                Finance.user_id == user_id,
                Finance.transaction_date >= month_start,
                Finance.transaction_date < next_month_start,
            )
            .order_by(Finance.transaction_date.asc(), Finance.id.asc())
            .limit(5000)
            .all()
        )

        rows = []
        for item in txs:
            rows.append(
                {
                    "transaction_date": item.transaction_date.strftime("%Y-%m-%d")
                    if item.transaction_date
                    else "",
                    "transaction_type": "수입" if item.transaction_type == "income" else "지출",
                    "title": item.title or "",
                    "category": item.category or "",
                    "payment_method": item.payment_method or "",
                    "amount": float(item.amount or 0),
                    "memo": item.memo or "",
                }
            )

        response, actual_format = export_to_excel(
            rows=rows,
            columns=[
                {"key": "transaction_date", "label": "거래일자"},
                {"key": "transaction_type", "label": "구분"},
                {"key": "title", "label": "항목명"},
                {"key": "category", "label": "카테고리"},
                {"key": "payment_method", "label": "결제방법"},
                {"key": "amount", "label": "금액"},
                {"key": "memo", "label": "메모"},
            ],
            filename=f"personal_finance_{year}_{month:02d}",
            sheet_name=f"{year}-{month:02d}",
        )

        log_audit_event(
            action="EXPORT",
            table_name="finances",
            new_values={
                "format": actual_format,
                "filters": export_filters,
                "row_count": len(rows),
                "status": "success",
            },
            user_id=user_id,
        )
        return response
    except Exception as exc:
        log_audit_event(
            action="EXPORT_FAILED",
            table_name="finances",
            new_values={
                "format": "excel",
                "filters": export_filters,
                "status": "failed",
                "reason": "unexpected_error",
                "error": str(exc)[:200],
            },
            user_id=user_id,
            use_async=False,
        )
        raise


@personal_bp.route("/finance/add", methods=["POST"])
@login_required
def finance_add():
    try:
        amount = Decimal(request.form["amount"].replace(",", "").strip())
        if amount <= 0:
            raise ValueError("금액은 0보다 커야 합니다")
        db.session.add(
            Finance(
                user_id=_current_user_id(),
                transaction_date=datetime.strptime(
                    request.form["transaction_date"], "%Y-%m-%d"
                ).date(),
                title=request.form["title"].strip(),
                amount=amount,
                transaction_type=request.form["transaction_type"],
                category=request.form.get("category") or "기타",
                payment_method=request.form.get("payment_method", ""),
                memo=request.form.get("memo", ""),
            )
        )
        db.session.commit()
        flash("✅ 거래 내역이 추가되었습니다.", "success")
    except (ValueError, InvalidOperation) as e:
        db.session.rollback()
        flash_route_error(e, "❌ 입력값 오류: ")
    return redirect(
        url_for(
            "personal.finance",
            year=request.form.get("year", datetime.now().year),
            month=request.form.get("month", datetime.now().month),
        )
    )


@personal_bp.route("/finance/delete/<int:finance_id>", methods=["POST"])
@login_required
def finance_delete(finance_id):
    f = _owned_query(Finance).filter(Finance.id == finance_id).first()
    if not f:
        flash("삭제할 거래를 찾을 수 없습니다.", "warning")
        return redirect(request.referrer or url_for("personal.finance"))
    db.session.delete(f)
    db.session.commit()
    flash("🗑️ 삭제되었습니다.", "success")
    return redirect(request.referrer or url_for("personal.finance"))
# 가계부 카테고리 관리 (FinanceCategory)
@personal_bp.route("/finance/categories")
@login_required
def finance_categories():
    """카테고리 관리 페이지"""
    user_id = _current_user_id()
    expense_cats = (
        FinanceCategory.query.filter(
            FinanceCategory.transaction_type == "expense",
            or_(
                FinanceCategory.user_id == user_id,
                FinanceCategory.user_id.is_(None),
            ),
        )
        .order_by(FinanceCategory.sort_order, FinanceCategory.name)
        .all()
    )
    income_cats = (
        FinanceCategory.query.filter(
            FinanceCategory.transaction_type == "income",
            or_(
                FinanceCategory.user_id == user_id,
                FinanceCategory.user_id.is_(None),
            ),
        )
        .order_by(FinanceCategory.sort_order, FinanceCategory.name)
        .all()
    )
    return render_template(
        "personal/finance_categories.html",
        expense_cats=expense_cats,
        income_cats=income_cats,
    )


@personal_bp.route("/finance/categories/add", methods=["POST"])
@login_required
def finance_category_add():
    name = request.form.get("name", "").strip()
    tx_type = request.form.get("transaction_type", "expense")
    if not name:
        flash("❌ 카테고리 이름을 입력하세요.", "danger")
        return redirect(url_for("personal.finance_categories"))
    if tx_type not in ("income", "expense"):
        tx_type = "expense"
    try:
        db.session.add(
            FinanceCategory(
                user_id=_current_user_id(),
                name=name,
                transaction_type=tx_type,
            )
        )
        db.session.commit()
        flash(f"✅ 카테고리 [{name}]이 추가되었습니다.", "success")
    except Exception as e:
        db.session.rollback()
        flash_route_error(e, "❌ 오류: ")
    return redirect(url_for("personal.finance_categories"))


@personal_bp.route("/finance/categories/<int:cid>/edit", methods=["POST"])
@login_required
def finance_category_edit(cid):
    user_id = _current_user_id()
    cat = (
        FinanceCategory.query
        .filter(
            FinanceCategory.id == cid,
            or_(
                FinanceCategory.user_id == user_id,
                FinanceCategory.user_id.is_(None),
            ),
        )
        .first()
    )
    if not cat:
        flash("카테고리를 찾을 수 없습니다.", "danger")
        return redirect(url_for("personal.finance_categories"))
    name = request.form.get("name", "").strip()
    if not name:
        flash("❌ 카테고리 이름을 입력하세요.", "danger")
        return redirect(url_for("personal.finance_categories"))
    try:
        if cat.user_id is None and current_user.role != "admin":
            flash("공용 카테고리는 관리자만 수정할 수 있습니다.", "warning")
            return redirect(url_for("personal.finance_categories"))
        cat.name = name
        if cat.user_id is None and current_user.role == "admin":
            # 관리자 수정 시 소유권을 명확히 남겨 이후 충돌을 줄인다.
            cat.user_id = user_id
        db.session.commit()
        flash(f"✅ [{name}]으로 수정되었습니다.", "success")
    except Exception as e:
        db.session.rollback()
        flash_route_error(e, "❌ 오류: ")
    return redirect(url_for("personal.finance_categories"))


@personal_bp.route("/finance/categories/<int:cid>/delete", methods=["POST"])
@login_required
def finance_category_delete(cid):
    user_id = _current_user_id()
    cat = (
        FinanceCategory.query
        .filter(
            FinanceCategory.id == cid,
            or_(
                FinanceCategory.user_id == user_id,
                FinanceCategory.user_id.is_(None),
            ),
        )
        .first()
    )
    if cat:
        if cat.user_id is None and current_user.role != "admin":
            flash("공용 카테고리는 관리자만 삭제할 수 있습니다.", "warning")
            return redirect(url_for("personal.finance_categories"))
        name = cat.name
        db.session.delete(cat)
        db.session.commit()
        flash(f"🗑️ [{name}] 카테고리가 삭제되었습니다.", "success")
    return redirect(url_for("personal.finance_categories"))
# 결제 수단 (통장/카드) 관리 (PaymentAccount)
@personal_bp.route("/finance/payment-accounts")
@login_required
def payment_accounts():
    """결제 수단(통장/카드) 관리 페이지"""
    accounts = _owned_query(PaymentAccount).order_by(
        PaymentAccount.account_type,
        PaymentAccount.sort_order,
        PaymentAccount.account_name,
    ).all()
    return render_template("personal/payment_accounts.html", accounts=accounts)


@personal_bp.route("/finance/payment-accounts/add", methods=["POST"])
@login_required
def payment_account_add():
    name = request.form.get("account_name", "").strip()
    if not name:
        flash("❌ 별칭을 입력하세요.", "danger")
        return redirect(url_for("personal.payment_accounts"))
    try:
        db.session.add(
            PaymentAccount(
                user_id=_current_user_id(),
                account_type=request.form.get("account_type", "bank"),
                bank_name=request.form.get("bank_name", "").strip() or None,
                account_name=name,
                account_number=request.form.get("account_number", "").strip() or None,
                notes=request.form.get("notes", "").strip() or None,
                is_active=True,
            )
        )
        db.session.commit()
        flash(f"✅ [{name}]이 등록되었습니다.", "success")
    except Exception as e:
        db.session.rollback()
        flash_route_error(e, "❌ 오류: ")
    return redirect(url_for("personal.payment_accounts"))


@personal_bp.route("/finance/payment-accounts/<int:aid>/edit", methods=["POST"])
@login_required
def payment_account_edit(aid):
    acc = _owned_query(PaymentAccount).filter(PaymentAccount.id == aid).first()
    if not acc:
        flash("항목을 찾을 수 없습니다.", "danger")
        return redirect(url_for("personal.payment_accounts"))
    try:
        acc.account_type = request.form.get("account_type", "bank")
        acc.bank_name = request.form.get("bank_name", "").strip() or None
        acc.account_name = request.form.get("account_name", "").strip()
        acc.account_number = request.form.get("account_number", "").strip() or None
        acc.notes = request.form.get("notes", "").strip() or None
        acc.is_active = request.form.get("is_active") == "1"
        db.session.commit()
        flash(f"✅ [{acc.account_name}] 정보가 수정되었습니다.", "success")
    except Exception as e:
        db.session.rollback()
        flash_route_error(e, "❌ 오류: ")
    return redirect(url_for("personal.payment_accounts"))


@personal_bp.route("/finance/payment-accounts/<int:aid>/delete", methods=["POST"])
@login_required
def payment_account_delete(aid):
    acc = _owned_query(PaymentAccount).filter(PaymentAccount.id == aid).first()
    if acc:
        name = acc.account_name
        db.session.delete(acc)
        db.session.commit()
        flash(f"🗑️ [{name}] 결제 수단이 삭제되었습니다.", "success")
    return redirect(url_for("personal.payment_accounts"))
# 캘린더
@personal_bp.route("/calendar")
@login_required
def calendar():
    return render_template("personal/calendar.html")
# 구독 관리 (Subscription)
@personal_bp.route("/subscriptions")
@login_required
def subscriptions():
    active = (
        _owned_query(Subscription).filter_by(is_active=True).order_by(Subscription.next_billing_date).all()
    )
    inactive = (
        _owned_query(Subscription).filter_by(is_active=False).order_by(Subscription.service_name).all()
    )

    # 월 환산 합계 (연간 ÷ 12, 분기 ÷ 3)
    monthly_total = sum(
        float(s.billing_amount or 0)
        / (12 if s.billing_cycle == "yearly" else 3 if s.billing_cycle == "quarterly" else 1)
        for s in active
    )
    return render_template(
        "personal/subscriptions.html",
        active_subs=active,
        inactive_subs=inactive,
        monthly_total=monthly_total,
        billing_cycles=BILLING_CYCLES,
        sub_categories=_get_sub_categories(),
        payment_methods=_get_payment_methods(),  # 동적: 등록된 통장/카드 목록 반영
        today=date.today(),
    )


@personal_bp.route("/subscriptions/add", methods=["POST"])
@login_required
def subscription_add():
    try:
        nbd_str = request.form.get("next_billing_date", "")
        db.session.add(
            Subscription(
                user_id=_current_user_id(),
                service_name=request.form["service_name"].strip(),
                billing_amount=Decimal(request.form["billing_amount"].replace(",", "").strip()),
                billing_cycle=request.form.get("billing_cycle", "monthly"),
                billing_day=request.form.get("billing_day", type=int),
                next_billing_date=datetime.strptime(nbd_str, "%Y-%m-%d").date()
                if nbd_str
                else None,
                payment_method=request.form.get("payment_method", ""),
                category=request.form.get("category", "기타"),
                is_active=True,
                notes=request.form.get("notes", ""),
            )
        )
        db.session.commit()
        flash(f'✅ "{request.form["service_name"]}" 구독이 등록되었습니다.', "success")
    except Exception as e:
        db.session.rollback()
        flash_route_error(e, "❌ 오류: ")
    return redirect(url_for("personal.subscriptions"))


@personal_bp.route("/subscriptions/edit/<int:sid>", methods=["POST"])
@login_required
def subscription_edit(sid):
    s = _owned_query(Subscription).filter(Subscription.id == sid).first()
    if not s:
        flash("항목을 찾을 수 없습니다.", "danger")
        return redirect(url_for("personal.subscriptions"))
    try:
        nbd_str = request.form.get("next_billing_date", "")
        s.service_name = request.form["service_name"].strip()
        s.billing_amount = Decimal(request.form["billing_amount"].replace(",", "").strip())
        s.billing_cycle = request.form.get("billing_cycle", "monthly")
        s.billing_day = request.form.get("billing_day", type=int)
        s.next_billing_date = datetime.strptime(nbd_str, "%Y-%m-%d").date() if nbd_str else None
        s.payment_method = request.form.get("payment_method", "")
        s.category = request.form.get("category", "기타")
        s.notes = request.form.get("notes", "")
        db.session.commit()
        flash(f'✅ "{s.service_name}" 정보가 수정되었습니다.', "success")
    except Exception as e:
        db.session.rollback()
        flash_route_error(e, "❌ 오류: ")
    return redirect(url_for("personal.subscriptions"))


@personal_bp.route("/subscriptions/toggle/<int:sid>", methods=["POST"])
@login_required
def subscription_toggle(sid):
    s = _owned_query(Subscription).filter(Subscription.id == sid).first()
    if s:
        s.is_active = not s.is_active
        db.session.commit()
        flash(
            f'"{s.service_name}" 구독이 {"활성화" if s.is_active else "비활성화"}되었습니다.',
            "success",
        )
    return redirect(url_for("personal.subscriptions"))


@personal_bp.route("/subscriptions/delete/<int:sid>", methods=["POST"])
@login_required
def subscription_delete(sid):
    s = _owned_query(Subscription).filter(Subscription.id == sid).first()
    if s:
        name = s.service_name
        db.session.delete(s)
        db.session.commit()
        flash(f'🗑️ "{name}" 구독이 삭제되었습니다.', "success")
    return redirect(url_for("personal.subscriptions"))
# 건강 일지 (Health)
@personal_bp.route("/health")
@login_required
def health():
    user_id = _current_user_id()
    since = date.today() - timedelta(days=59)
    month_since = date.today() - timedelta(days=29)
    logs = (
        HealthLog.query.filter(
            HealthLog.user_id == user_id,
            HealthLog.log_date >= since,
        )
        .order_by(HealthLog.log_date.asc())
        .all()
    )
    month_logs = (
        HealthLog.query.filter(
            HealthLog.user_id == user_id,
            HealthLog.log_date >= month_since,
        )
        .order_by(HealthLog.log_date.asc())
        .all()
    )
    recent_logs = (
        HealthLog.query
        .filter(HealthLog.user_id == user_id)
        .order_by(HealthLog.log_date.desc(), HealthLog.id.desc())
        .limit(30)
        .all()
    )

    week_logs = recent_logs[:7]

    def _average_decimal(values):
        valid = [Decimal(str(value)) for value in values if value is not None]
        if not valid:
            return None
        return sum(valid) / Decimal(len(valid))

    def _average_int(values):
        valid = [int(value) for value in values if value is not None]
        if not valid:
            return None
        return round(sum(valid) / len(valid))

    latest = recent_logs[0] if recent_logs else None
    previous = recent_logs[1] if len(recent_logs) > 1 else None

    latest_weight = float(latest.weight) if latest and latest.weight is not None else None
    previous_weight = float(previous.weight) if previous and previous.weight is not None else None
    latest_glucose = latest.blood_sugar if latest and latest.blood_sugar is not None else None
    previous_glucose = previous.blood_sugar if previous and previous.blood_sugar is not None else None

    weight_change = None
    if latest_weight is not None and previous_weight is not None:
        weight_change = round(latest_weight - previous_weight, 1)

    glucose_change = None
    if latest_glucose is not None and previous_glucose is not None:
        glucose_change = latest_glucose - previous_glucose

    avg_weight = _average_decimal([log.weight for log in week_logs])
    avg_glucose = _average_int([log.blood_sugar for log in week_logs])
    avg_systolic = _average_int([log.blood_pressure_systolic for log in week_logs])
    avg_diastolic = _average_int([log.blood_pressure_diastolic for log in week_logs])

    meds_rate = 0
    if week_logs:
        meds_rate = round((sum(1 for log in week_logs if log.meds_taken) / len(week_logs)) * 100)

    month_record_count = len(month_logs)
    month_high_glucose_count = sum(
        1 for log in month_logs if log.blood_sugar is not None and log.blood_sugar >= 126
    )
    month_high_pressure_count = sum(
        1
        for log in month_logs
        if (log.blood_pressure_systolic is not None and log.blood_pressure_systolic >= 140)
        or (log.blood_pressure_diastolic is not None and log.blood_pressure_diastolic >= 90)
    )
    month_meds_rate = 0
    if month_logs:
        month_meds_rate = round(
            (sum(1 for log in month_logs if log.meds_taken) / len(month_logs)) * 100
        )

    glucose_info = _classify_glucose_level(latest.blood_sugar if latest else None)
    pressure_info = _classify_pressure_level(
        latest.blood_pressure_systolic if latest else None,
        latest.blood_pressure_diastolic if latest else None,
    )
    weekly_comment = _build_health_weekly_comment(
        weight_change, avg_glucose, avg_systolic, avg_diastolic, meds_rate
    )

    logs_by_date = {}
    for log in recent_logs:
        logs_by_date[log.log_date.isoformat()] = {
            "log_time": log.log_time or "",
            "weight": float(log.weight) if log.weight is not None else "",
            "blood_sugar": log.blood_sugar or "",
            "blood_pressure_systolic": log.blood_pressure_systolic or "",
            "blood_pressure_diastolic": log.blood_pressure_diastolic or "",
            "meds_taken": bool(log.meds_taken),
            "meds_name": log.meds_name or "",
            "memo": log.memo or "",
        }

    return render_template(
        "personal/health.html",
        recent_logs=recent_logs,
        latest=latest,
        previous=previous,
        avg_weight=float(avg_weight) if avg_weight is not None else None,
        avg_glucose=avg_glucose,
        avg_systolic=avg_systolic,
        avg_diastolic=avg_diastolic,
        meds_rate=meds_rate,
        month_record_count=month_record_count,
        month_high_glucose_count=month_high_glucose_count,
        month_high_pressure_count=month_high_pressure_count,
        month_meds_rate=month_meds_rate,
        glucose_info=glucose_info,
        pressure_info=pressure_info,
        weekly_comment=weekly_comment,
        weight_change=weight_change,
        glucose_change=glucose_change,
        logs_by_date=logs_by_date,
        chart_dates=[l.log_date.strftime("%m/%d") for l in logs],
        chart_weight=[float(l.weight) if l.weight else None for l in logs],
        chart_glucose=[int(l.blood_sugar) if l.blood_sugar else None for l in logs],
        chart_pressure_systolic=[
            int(l.blood_pressure_systolic) if l.blood_pressure_systolic else None for l in logs
        ],
        chart_pressure_diastolic=[
            int(l.blood_pressure_diastolic) if l.blood_pressure_diastolic else None for l in logs
        ],
        today=date.today(),
    )


@personal_bp.route("/health/add", methods=["POST"])
@login_required
def health_add():
    user_id = _current_user_id()
    try:
        log_date = datetime.strptime(request.form["log_date"], "%Y-%m-%d").date()
        log_time = request.form.get("log_time", "").strip()
        w = request.form.get("weight", "").strip()
        g = request.form.get("blood_sugar", "").strip()
        bps = request.form.get("blood_pressure_systolic", "").strip()
        bpd = request.form.get("blood_pressure_diastolic", "").strip()

        log = HealthLog.query.filter_by(log_date=log_date, user_id=user_id).first() or HealthLog(
            log_date=log_date,
            user_id=user_id,
        )
        if not log.id:
            db.session.add(log)

        log.log_time = log_time or None
        log.weight = Decimal(w) if w else None
        log.blood_sugar = int(g) if g else None
        log.blood_pressure_systolic = int(bps) if bps else None
        log.blood_pressure_diastolic = int(bpd) if bpd else None
        log.meds_taken = bool(request.form.get("meds_taken"))
        log.meds_name = request.form.get("meds_name", "").strip() or None
        log.memo = request.form.get("memo", "").strip() or None

        db.session.commit()
        flash("✅ 건강 기록이 저장되었습니다.", "success")
    except Exception as e:
        db.session.rollback()
        flash_route_error(e, "❌ 저장 오류: ")
    return redirect(url_for("personal.health"))


@personal_bp.route("/health/delete/<int:lid>", methods=["POST"])
@login_required
def health_delete(lid):
    log = _owned_query(HealthLog).filter(HealthLog.id == lid).first()
    if log:
        db.session.delete(log)
        db.session.commit()
        flash("🗑️ 삭제되었습니다.", "success")
    return redirect(url_for("personal.health"))
# 갤러리 (Gallery)
@personal_bp.route("/gallery")
@login_required
def gallery():
    user_id = _current_user_id()
    cat = request.args.get("cat", "")
    q = GalleryImage.query.filter(GalleryImage.user_id == user_id)
    if cat:
        q = q.filter(GalleryImage.category == cat)
    images = q.order_by(GalleryImage.created_at.desc()).all()
    categories = [
        c[0]
        for c in db.session.query(GalleryImage.category)
        .filter(
            GalleryImage.user_id == user_id,
            GalleryImage.category.isnot(None),
        )
        .distinct()
        .all()
        if c[0]
    ]
    return render_template(
        "personal/gallery.html", images=images, categories=categories, active_cat=cat
    )


@personal_bp.route("/gallery/upload", methods=["POST"])
@login_required
def gallery_upload():
    user_id = _current_user_id()
    files = request.files.getlist("images")
    if not files or all(f.filename == "" for f in files):
        flash("❌ 업로드할 파일을 선택해 주세요.", "danger")
        return redirect(url_for("personal.gallery"))

    category = request.form.get("category", "").strip() or None
    title = request.form.get("title", "").strip() or None
    ok = 0

    upload_base = _gallery_upload_base()
    gallery_dir = upload_base / "gallery"

    for f in files:
        if not f or not f.filename:
            continue
        if not _allowed_image(f.filename):
            flash(f'❌ "{f.filename}" — 지원하지 않는 형식입니다.', "warning")
            continue
        try:
            info = _process_image(f)
            db.session.add(
                GalleryImage(
                    user_id=user_id,
                    original_filename=info["original_filename"],
                    stored_filename=info["stored_filename"],
                    thumbnail_filename=info["thumbnail_filename"],
                    title=title,
                    category=category,
                    file_size=info["file_size"],
                    width=info["width"],
                    height=info["height"],
                )
            )
            ok += 1
        except Exception as e:
            current_app.logger.error(f"이미지 업로드 오류: {e}")
            flash(f'❌ "{f.filename}" 처리 중 오류가 발생했습니다.', "danger")

    if ok:
        db.session.commit()
        flash(f"{ok}장이 업로드되었습니다.", "success")
        current_app.logger.info("[gallery] 저장 경로 점검: %s", gallery_dir)
    return redirect(url_for("personal.gallery"))


@personal_bp.route("/gallery/delete/<int:gid>", methods=["POST"])
@login_required
def gallery_delete(gid):
    img = _owned_query(GalleryImage).filter(GalleryImage.id == gid).first()
    if not img:
        flash("이미지를 찾을 수 없습니다.", "danger")
        return redirect(url_for("personal.gallery"))
    base = _gallery_upload_base()
    for p in [
        base / "gallery" / (img.stored_filename or ""),
        base / "thumbnails" / (img.thumbnail_filename or ""),
    ]:
        try:
            if p.exists():
                p.unlink(missing_ok=True)
        except (PermissionError, OSError) as e:
            current_app.logger.warning("갤러리 파일 삭제 실패(%s): %s", p, e)
    db.session.delete(img)
    db.session.commit()
    flash("🗑️ 이미지가 삭제되었습니다.", "success")
    return redirect(url_for("personal.gallery"))
# 대출 관리 (Loan)
@personal_bp.route("/finance/loans")
@login_required
def loans():
    """대출/부채 관리 페이지"""
    all_loans = _owned_query(Loan).order_by(Loan.is_active.desc(), Loan.end_date).all()
    total_debt = sum(float(l.remaining or l.principal or 0) for l in all_loans if l.is_active)
    total_monthly = sum(float(l.monthly_payment or 0) for l in all_loans if l.is_active)
    return render_template(
        "personal/loans.html",
        loans=all_loans,
        total_debt=total_debt,
        total_monthly=total_monthly,
        today=date.today(),
    )


@personal_bp.route("/finance/loans/add", methods=["POST"])
@login_required
def loan_add():
    name = request.form.get("loan_name", "").strip()
    if not name:
        flash("❌ 대출 상품명을 입력하세요.", "danger")
        return redirect(url_for("personal.loans"))

    principal, principal_filled = _숫자입력_해석(request.form.get("principal"))
    remaining, remaining_filled = _숫자입력_해석(request.form.get("remaining"))
    monthly_payment, monthly_filled = _숫자입력_해석(request.form.get("monthly_payment"))
    interest_rate, rate_filled = _숫자입력_해석(request.form.get("interest_rate"))
    start_date, start_filled = _날짜입력_해석(request.form.get("start_date"))
    end_date, end_filled = _날짜입력_해석(request.form.get("end_date"))

    if not principal_filled or principal is None:
        flash("❌ 원금은 숫자로 입력해 주세요.", "danger")
        return redirect(url_for("personal.loans"))
    if remaining_filled and remaining is None:
        flash("❌ 잔여원금은 숫자로 입력해 주세요.", "danger")
        return redirect(url_for("personal.loans"))
    if monthly_filled and monthly_payment is None:
        flash("❌ 월 납입액은 숫자로 입력해 주세요.", "danger")
        return redirect(url_for("personal.loans"))
    if rate_filled and interest_rate is None:
        flash("❌ 연 이율은 숫자로 입력해 주세요.", "danger")
        return redirect(url_for("personal.loans"))
    if start_filled and start_date is None:
        flash("❌ 대출 개시일 형식이 올바르지 않습니다.", "danger")
        return redirect(url_for("personal.loans"))
    if end_filled and end_date is None:
        flash("❌ 만기일 형식이 올바르지 않습니다.", "danger")
        return redirect(url_for("personal.loans"))

    try:
        db.session.add(
            Loan(
                user_id=_current_user_id(),
                lender_name=request.form.get("lender_name", "").strip(),
                loan_name=name,
                principal=principal,
                remaining=remaining,
                interest_rate=interest_rate,
                monthly_payment=monthly_payment,
                start_date=start_date,
                end_date=end_date,
                is_active=True,
                notes=request.form.get("notes", "").strip() or None,
            )
        )
        db.session.commit()
        flash(f"✅ [{name}] 대출이 등록되었습니다.", "success")
    except Exception as e:
        db.session.rollback()
        flash_route_error(e, "❌ 오류: ")
    return redirect(url_for("personal.loans"))


@personal_bp.route("/finance/loans/<int:lid>/edit", methods=["POST"])
@login_required
def loan_edit(lid):
    loan = _owned_query(Loan).filter(Loan.id == lid).first()
    if not loan:
        flash("대출 항목을 찾을 수 없습니다.", "danger")
        return redirect(url_for("personal.loans"))

    principal, principal_filled = _숫자입력_해석(request.form.get("principal"))
    remaining, remaining_filled = _숫자입력_해석(request.form.get("remaining"))
    monthly_payment, monthly_filled = _숫자입력_해석(request.form.get("monthly_payment"))
    interest_rate, rate_filled = _숫자입력_해석(request.form.get("interest_rate"))
    start_date, start_filled = _날짜입력_해석(request.form.get("start_date"))
    end_date, end_filled = _날짜입력_해석(request.form.get("end_date"))

    if not principal_filled or principal is None:
        flash("❌ 원금은 숫자로 입력해 주세요.", "danger")
        return redirect(url_for("personal.loans"))
    if remaining_filled and remaining is None:
        flash("❌ 잔여원금은 숫자로 입력해 주세요.", "danger")
        return redirect(url_for("personal.loans"))
    if monthly_filled and monthly_payment is None:
        flash("❌ 월 납입액은 숫자로 입력해 주세요.", "danger")
        return redirect(url_for("personal.loans"))
    if rate_filled and interest_rate is None:
        flash("❌ 연 이율은 숫자로 입력해 주세요.", "danger")
        return redirect(url_for("personal.loans"))
    if start_filled and start_date is None:
        flash("❌ 대출 개시일 형식이 올바르지 않습니다.", "danger")
        return redirect(url_for("personal.loans"))
    if end_filled and end_date is None:
        flash("❌ 만기일 형식이 올바르지 않습니다.", "danger")
        return redirect(url_for("personal.loans"))

    try:
        loan.lender_name = request.form.get("lender_name", "").strip()
        loan_name = request.form.get("loan_name", "").strip()
        if not loan_name:
            flash("❌ 대출 상품명을 입력하세요.", "danger")
            return redirect(url_for("personal.loans"))
        loan.loan_name = loan_name
        loan.principal = principal
        loan.remaining = remaining
        loan.interest_rate = interest_rate
        loan.monthly_payment = monthly_payment
        loan.start_date = start_date
        loan.end_date = end_date
        loan.is_active = request.form.get("is_active") == "1"
        loan.notes = request.form.get("notes", "").strip() or None
        db.session.commit()
        flash(f"✅ [{loan.loan_name}] 정보가 수정되었습니다.", "success")
    except Exception as e:
        db.session.rollback()
        flash_route_error(e, "❌ 오류: ")
    return redirect(url_for("personal.loans"))


@personal_bp.route("/finance/loans/<int:lid>/delete", methods=["POST"])
@login_required
def loan_delete(lid):
    loan = _owned_query(Loan).filter(Loan.id == lid).first()
    if loan:
        name = loan.loan_name
        db.session.delete(loan)
        db.session.commit()
        flash(f"🗑️ [{name}] 대출이 삭제되었습니다.", "success")
    return redirect(url_for("personal.loans"))
