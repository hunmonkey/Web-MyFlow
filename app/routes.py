from types import SimpleNamespace

from flask import Blueprint, current_app, jsonify, render_template

from app.models import Profile, Project

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def home():
    profile = Profile.query.order_by(Profile.id.asc()).first()
    if profile is None:
        profile = SimpleNamespace(
            name=current_app.config["OWNER_NAME"],
            headline="개인 사이트를 준비 중입니다.",
            intro="이 문구는 첫 실행 시 기본 안내입니다. 원하시는 소개 문구로 바꿔주세요.",
            contact_email=current_app.config.get("CONTACT_EMAIL", ""),
        )

    projects = (
        Project.query.filter_by(is_active=True)
        .order_by(Project.sort_order.asc(), Project.id.desc())
        .all()
    )

    return render_template(
        "index.html",
        site_title=current_app.config["SITE_TITLE"],
        profile=profile,
        projects=projects,
    )


@main_bp.get("/healthz")
def health_check():
    return jsonify({"ok": True, "service": "watermelon-personal"}), 200
