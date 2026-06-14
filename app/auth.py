from functools import wraps

from flask import abort, redirect, url_for
from flask_login import current_user
from werkzeug.security import generate_password_hash

from models import db, User


def create_initial_admin(username, password):
    existing_admin = User.query.filter_by(role="admin").first()

    if existing_admin:
        return

    admin = User(
        username=username,
        password_hash=generate_password_hash(password),
        role="admin",
        active=True
    )

    db.session.add(admin)
    db.session.commit()


def role_required(*roles):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("login"))

            if current_user.role not in roles:
                abort(403)

            return func(*args, **kwargs)
        return wrapper
    return decorator