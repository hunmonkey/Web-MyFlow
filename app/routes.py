from flask import Blueprint, jsonify, redirect, url_for
from flask_login import current_user

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("personal.index"))
    return redirect(url_for("auth.login"))


@main_bp.get("/healthz")
def health_check():
    return jsonify({"ok": True, "service": "watermelon-personal"}), 200
