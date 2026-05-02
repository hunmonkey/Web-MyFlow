from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.models import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("personal.index"))

    if request.method == "POST":
        ajax = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")
        )
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user and user.is_active and user.check_password(password):
            login_user(user)
            target_url = url_for("personal.index")
            if ajax:
                return jsonify({
                    "success": True,
                    "message": f"어서오세요, {user.display_name}님",
                    "redirect_url": target_url,
                })
            return redirect(target_url)

        message = "아이디 또는 비밀번호가 맞지 않습니다."
        if ajax:
            return jsonify({"success": False, "message": message})
        flash(message, "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
