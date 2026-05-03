from datetime import datetime

from flask_login import UserMixin

from app.extensions import db


class Profile(db.Model):
    __tablename__ = "profiles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    headline = db.Column(db.String(200), nullable=False)
    intro = db.Column(db.Text, nullable=False)
    contact_email = db.Column(db.String(120), nullable=True)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(255), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    display_name = db.Column(db.String(100), default="사용자", nullable=False)
    profile_image = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_admin = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def set_password(self, password: str) -> None:
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"


class UserFormatSetting(db.Model):
    __tablename__ = "user_format_settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    date_format = db.Column(db.String(20), nullable=False, default="YYYY-MM-DD")
    datetime_format = db.Column(db.String(30), nullable=False, default="YYYY-MM-DD HH:mm")
    currency_symbol = db.Column(db.String(10), nullable=False, default="원")
    number_grouping = db.Column(db.Boolean, nullable=False, default=True)
    preferred_theme = db.Column(db.String(10), nullable=False, default="system")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user = db.relationship("User", lazy="joined")


class BackupSchedule(db.Model):
    __tablename__ = "backup_schedules"

    id = db.Column(db.Integer, primary_key=True)
    backup_type = db.Column(db.String(10), nullable=False, unique=True, index=True)
    is_enabled = db.Column(db.Boolean, nullable=False, default=False)
    repeat_type = db.Column(db.String(10), nullable=False, default="mask1")
    weekday = db.Column(db.Integer, nullable=False, default=1)
    run_time = db.Column(db.String(5), nullable=False, default="03:00")
    last_run_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class FinanceTransaction(db.Model):
    __tablename__ = "finance_transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transaction_date = db.Column(db.Date, nullable=False, index=True)
    transaction_type = db.Column(db.String(10), nullable=False)  # income | expense
    title = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(80), nullable=False, default="기타")
    payment_method = db.Column(db.String(50), nullable=False, default="현금")
    memo = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", lazy="joined")


class PersonalSubscription(db.Model):
    __tablename__ = "personal_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    service_name = db.Column(db.String(120), nullable=False)
    billing_amount = db.Column(db.Integer, nullable=False, default=0)
    billing_cycle = db.Column(db.String(20), nullable=False, default="monthly")
    next_billing_date = db.Column(db.Date, nullable=True, index=True)
    payment_method = db.Column(db.String(50), nullable=False, default="카드")
    category = db.Column(db.String(60), nullable=False, default="기타")
    notes = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user = db.relationship("User", lazy="joined")


class HealthLog(db.Model):
    __tablename__ = "health_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    log_date = db.Column(db.Date, nullable=False, index=True)
    log_time = db.Column(db.String(5), nullable=True)
    weight = db.Column(db.Float, nullable=True)
    blood_sugar = db.Column(db.Integer, nullable=True)
    blood_pressure_systolic = db.Column(db.Integer, nullable=True)
    blood_pressure_diastolic = db.Column(db.Integer, nullable=True)
    meds_taken = db.Column(db.Boolean, nullable=False, default=False)
    meds_name = db.Column(db.String(200), nullable=True)
    memo = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user = db.relationship("User", lazy="joined")


class PersonalSchedule(db.Model):
    __tablename__ = "personal_schedules"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(200), nullable=False)
    start_at = db.Column(db.DateTime, nullable=False, index=True)
    end_at = db.Column(db.DateTime, nullable=True)
    all_day = db.Column(db.Boolean, nullable=False, default=False)
    category = db.Column(db.String(30), nullable=False, default="personal")
    color = db.Column(db.String(20), nullable=False, default="#0d6efd")
    description = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user = db.relationship("User", lazy="joined")


class PersonalPaletteSet(db.Model):
    __tablename__ = "personal_palette_sets"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(120), nullable=False, default="색상 조합")
    box1_hex = db.Column(db.String(7), nullable=True, default="")
    box1_rgb = db.Column(db.String(20), nullable=True, default="")
    box2_hex = db.Column(db.String(7), nullable=True, default="")
    box2_rgb = db.Column(db.String(20), nullable=True, default="")
    box3_hex = db.Column(db.String(7), nullable=True, default="")
    box3_rgb = db.Column(db.String(20), nullable=True, default="")
    box4_hex = db.Column(db.String(7), nullable=True, default="")
    box4_rgb = db.Column(db.String(20), nullable=True, default="")
    description = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", lazy="joined")


class PersonalShortcut(db.Model):
    __tablename__ = "personal_shortcuts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    site_url = db.Column(db.String(500), nullable=False)
    site_name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.String(300), nullable=True)
    note = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", lazy="joined")
