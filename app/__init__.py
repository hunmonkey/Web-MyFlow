import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g

from config import config_map
from app.extensions import db
from app.models import Profile, Project

_WEAK_SECRET_KEY_VALUES = {
    "",
    "dev-local-only-change-me",
    "change-me",
}


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _enforce_production_security_baseline(app: Flask, env: str) -> None:
    if env != "production":
        return

    secret_key = str(app.config.get("SECRET_KEY") or "").strip()
    if not secret_key or secret_key in _WEAK_SECRET_KEY_VALUES or len(secret_key) < 32:
        raise RuntimeError(
            "운영 환경 보안 설정 오류: SECRET_KEY를 32자 이상 강한 값으로 설정해 주세요."
        )


def _seed_default_content(app: Flask) -> None:
    profile = Profile.query.first()
    if profile is None:
        db.session.add(
            Profile(
                name=app.config["OWNER_NAME"],
                headline="문제를 풀어내는 개인 개발 공간",
                intro=(
                    "이곳은 재훈 님의 개인 사이트 기본 화면입니다. "
                    "자기소개, 포트폴리오, 연락처를 여기에 정리해 두면 됩니다."
                ),
                contact_email=app.config.get("CONTACT_EMAIL", ""),
            )
        )

    if Project.query.count() == 0:
        db.session.add_all(
            [
                Project(
                    title="프로젝트 소개 준비",
                    summary="첫 번째 프로젝트 설명을 이 칸에 적어 주세요.",
                    link="",
                    sort_order=1,
                    is_active=True,
                ),
                Project(
                    title="운영서버 연결 준비",
                    summary="Docker + PostgreSQL 구성으로 개인 사이트를 독립 운영합니다.",
                    link="",
                    sort_order=2,
                    is_active=True,
                ),
            ]
        )

    db.session.commit()


def create_app(env_name: str | None = None) -> Flask:
    load_dotenv()

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    env = env_name or os.environ.get("FLASK_ENV", "default")
    config_class = config_map.get(env, config_map["default"])
    app.config.from_object(config_class)
    _enforce_production_security_baseline(app, env)

    upload_dir = Path(app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    (Path(app.root_path).parent / "instance").mkdir(parents=True, exist_ok=True)

    db.init_app(app)

    from app.routes import main_bp
    from app.routes_personal import personal_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(personal_bp)

    @app.context_processor
    def inject_csp_nonce():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    with app.app_context():
        if _is_truthy(os.environ.get("AUTO_DB_CREATE", "1")):
            db.create_all()
        if _is_truthy(os.environ.get("AUTO_SEED_DATA", "1")):
            _seed_default_content(app)

    return app
