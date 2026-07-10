"""Passwort-Policy-Prüfung und Passwort-Reset-Tokens."""
import re
import secrets
from datetime import datetime, timedelta

from models import db, PasswordResetToken
from services.settings import setting_value


def password_policy():
    return {
        "min_length": int(setting_value("password_min_length", 8) or 8),
        "require_upper": setting_value("password_require_upper", "0") == "1",
        "require_lower": setting_value("password_require_lower", "0") == "1",
        "require_digit": setting_value("password_require_digit", "0") == "1",
        "require_special": setting_value("password_require_special", "0") == "1",
    }


def password_policy_text(policy=None):
    policy = policy or password_policy()
    parts = [f"mindestens {policy['min_length']} Zeichen"]
    if policy["require_upper"]:
        parts.append("mindestens 1 Großbuchstabe")
    if policy["require_lower"]:
        parts.append("mindestens 1 Kleinbuchstabe")
    if policy["require_digit"]:
        parts.append("mindestens 1 Zahl")
    if policy["require_special"]:
        parts.append("mindestens 1 Sonderzeichen")
    return ", ".join(parts) + "."


def validate_password_policy(password):
    policy = password_policy()
    errors = []
    if len(password or "") < policy["min_length"]:
        errors.append(f"mindestens {policy['min_length']} Zeichen")
    if policy["require_upper"] and not re.search(r"[A-ZÄÖÜ]", password or ""):
        errors.append("mindestens 1 Großbuchstabe")
    if policy["require_lower"] and not re.search(r"[a-zäöüß]", password or ""):
        errors.append("mindestens 1 Kleinbuchstabe")
    if policy["require_digit"] and not re.search(r"\d", password or ""):
        errors.append("mindestens 1 Zahl")
    if policy["require_special"] and not re.search(r"[^A-Za-zÄÖÜäöüß0-9]", password or ""):
        errors.append("mindestens 1 Sonderzeichen")
    return errors


def create_password_reset_token(user):
    """Erzeugt einen 24-Stunden-Einmal-Link für Passwort-Reset."""
    token = secrets.token_urlsafe(32)
    reset = PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.session.add(reset)
    return reset


def active_password_reset_tokens():
    now = datetime.utcnow()
    return PasswordResetToken.query.filter(
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at >= now,
    ).order_by(PasswordResetToken.expires_at.asc()).all()
