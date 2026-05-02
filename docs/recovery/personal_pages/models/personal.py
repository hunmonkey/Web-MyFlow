"""
models/personal.py - 개인 모듈 데이터 모델
============================================================
포함: 재정(Finance), 구독관리(Subscription), 건강일지(HealthLog), 갤러리(Gallery)
      FinanceCategory(가계부 카테고리), PaymentAccount(통장/카드 결제수단),
      Loan(대출/부채 관리)
"""

from datetime import datetime, timezone
from app.extensions import db


class FinanceCategory(db.Model):
    """
    가계부 카테고리 마스터 — 사용자가 설정에서 직접 추가/수정/삭제
    transaction_type: 'income' 또는 'expense'
    """

    __tablename__ = "finance_categories"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="소유 사용자 ID (NULL이면 공용 카테고리)",
    )
    name = db.Column(db.String(50), nullable=False)
    transaction_type = db.Column(db.String(10), nullable=False, comment="income 또는 expense")
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    user = db.relationship("User", lazy="joined")

    def __repr__(self):
        return f"<FinanceCategory {self.name} [{self.transaction_type}]>"


class PaymentAccount(db.Model):
    """
    통장/카드 결제 수단 마스터 — 사용자가 등록한 계좌·카드 목록
    account_type: 'bank' (통장/계좌) 또는 'card' (카드)
    """

    __tablename__ = "payment_accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="소유 사용자 ID",
    )
    account_type = db.Column(db.String(10), nullable=False, comment="bank(통장) 또는 card(카드)")
    bank_name = db.Column(db.String(50), comment="은행/카드사명")
    account_name = db.Column(db.String(100), nullable=False, comment="별칭 (예: 우리은행 월급통장)")
    account_number = db.Column(
        db.String(30), comment="계좌번호 또는 카드번호 (마지막 4자리만 저장 권장)"
    )
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    notes = db.Column(db.String(200))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    user = db.relationship("User", lazy="joined")

    @property
    def display_name(self):
        """드롭다운 표시용: 'KB카드 · 신용카드' 형식"""
        prefix = "🏦" if self.account_type == "bank" else "💳"
        return f"{prefix} {self.account_name}"

    def __repr__(self):
        return f"<PaymentAccount {self.account_name} [{self.account_type}]>"


class Finance(db.Model):
    """수입/지출 재정 관리"""

    __tablename__ = "finances"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="소유 사용자 ID",
    )
    transaction_date = db.Column(db.Date, nullable=False, comment="거래일")
    title = db.Column(db.String(200), nullable=False, comment="항목명")
    amount = db.Column(db.Numeric(15, 2), nullable=False, comment="금액")
    transaction_type = db.Column(
        db.String(10), nullable=False, comment="유형: income(수입)/expense(지출)"
    )
    category = db.Column(db.String(50), comment="분류 (식비/교통/의료/급여 등)")
    payment_method = db.Column(db.String(100), comment="결제 방법 (통장/카드 명칭)")
    memo = db.Column(db.String(500), comment="메모")

    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    user = db.relationship("User", lazy="joined")

    def __repr__(self):
        return f"<Finance {self.title} {self.amount}>"


class Subscription(db.Model):
    """
    정기 구독 서비스 관리
    Netflix, 유튜브 프리미엄 등 매월 자동결제 서비스 추적
    결제 3일 전 자동 알림 생성 (Celery Task)
    """

    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="소유 사용자 ID",
    )
    service_name = db.Column(db.String(100), nullable=False, comment="서비스명")
    billing_amount = db.Column(db.Numeric(12, 2), nullable=False, comment="결제금액")
    billing_cycle = db.Column(
        db.String(20), default="monthly", comment="결제주기: monthly/yearly/quarterly"
    )
    billing_day = db.Column(db.Integer, comment="결제일 (매월 N일)")
    next_billing_date = db.Column(db.Date, comment="다음 결제예정일")
    payment_method = db.Column(db.String(50), comment="결제수단")
    category = db.Column(db.String(50), comment="구독 분류 (OTT/음악/클라우드/업무 등)")
    is_active = db.Column(db.Boolean, default=True, comment="구독 활성 여부")
    notes = db.Column(db.Text, comment="비고")

    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    user = db.relationship("User", lazy="joined")

    def __repr__(self):
        return f"<Subscription {self.service_name}>"


class HealthLog(db.Model):
    """
    건강 일지
    체중 / 혈당 / 복약 기록
    대시보드에서 이중축 차트(체중 vs 혈당)로 시각화
    """

    __tablename__ = "health_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="소유 사용자 ID",
    )
    log_date = db.Column(db.Date, nullable=False, comment="기록일")
    log_time = db.Column(db.String(10), comment="기록 시각 (예: 07:30)")
    weight = db.Column(db.Numeric(5, 2), comment="체중 (kg)")
    blood_sugar = db.Column(db.Integer, comment="혈당 (mg/dL)")
    blood_pressure_systolic = db.Column(db.Integer, comment="혈압 수축기 (mmHg)")
    blood_pressure_diastolic = db.Column(db.Integer, comment="혈압 이완기 (mmHg)")
    meds_taken = db.Column(db.Boolean, default=False, comment="복약 여부")
    meds_name = db.Column(db.String(200), comment="복약 약품명")
    memo = db.Column(db.Text, comment="비고")

    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    user = db.relationship("User", lazy="joined")

    def __repr__(self):
        return f"<HealthLog {self.log_date}>"


class GalleryImage(db.Model):
    """
    갤러리 이미지
    업로드 시 원본 파일명/형식 유지 + 썸네일 생성
    UI: Masonry Grid + Lightbox
    """

    __tablename__ = "gallery_images"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="소유 사용자 ID",
    )
    original_filename = db.Column(db.String(255), comment="원본 파일명")
    stored_filename = db.Column(db.String(255), nullable=False, comment="서버 저장 파일명")
    thumbnail_filename = db.Column(db.String(255), comment="썸네일 저장 파일명")
    title = db.Column(db.String(200), comment="이미지 제목")
    description = db.Column(db.Text, comment="설명")
    category = db.Column(db.String(50), comment="분류 (개인/여행/가족 등)")
    tags = db.Column(db.String(200), comment="태그")
    file_size = db.Column(db.Integer, comment="파일 크기 (bytes)")
    width = db.Column(db.Integer, comment="이미지 너비 (px)")
    height = db.Column(db.Integer, comment="이미지 높이 (px)")
    taken_at = db.Column(db.DateTime, comment="촬영일시 (EXIF 추출)")

    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    user = db.relationship("User", lazy="joined")

    @property
    def thumbnail_url(self) -> str:
        return (
            f"/static/uploads/thumbnails/{self.thumbnail_filename}"
            if self.thumbnail_filename
            else self.image_url
        )

    @property
    def image_url(self) -> str:
        return f"/static/uploads/gallery/{self.stored_filename}"

    def __repr__(self):
        return f"<GalleryImage {self.stored_filename}>"


class Loan(db.Model):
    """
    대출/부채 관리
    은행 대출, 카드론, 사금융 등 모든 부채 항목을 통합 관리합니다.
    """

    __tablename__ = "loans"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="소유 사용자 ID",
    )
    lender_name = db.Column(
        db.String(100), nullable=False, comment="대출 기관명 (예: 국민은행, 카카오뱅크)"
    )
    loan_name = db.Column(
        db.String(200),
        nullable=False,
        comment="대출 상품명 (예: 주택담보대출, 신용대출)",
    )
    principal = db.Column(db.Numeric(15, 0), nullable=False, comment="최초 원금 (원)")
    remaining = db.Column(db.Numeric(15, 0), comment="잔여 원금 (원, 없으면 원금과 동일)")
    interest_rate = db.Column(db.Numeric(5, 3), comment="연 이율 (%)")
    monthly_payment = db.Column(db.Numeric(12, 0), comment="월 납입액 (원)")
    start_date = db.Column(db.Date, comment="대출 개시일")
    end_date = db.Column(db.Date, comment="만기일")
    is_active = db.Column(db.Boolean, default=True, comment="상환 중 여부 (False = 상환 완료)")
    notes = db.Column(db.Text, comment="비고")

    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    user = db.relationship("User", lazy="joined")

    def __repr__(self):
        return f"<Loan {self.loan_name} [{self.lender_name}]>"
