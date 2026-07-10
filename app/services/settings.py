"""Zugriff auf die generische App-Einstellungen-Tabelle (AppSetting) sowie
Erkennung, welche Schlüssel geheime Zugangsdaten sind (nie in Export/Backup)."""
from models import db, AppSetting


def setting_value(key, default=None):
    setting = AppSetting.query.filter_by(key=key).first()
    return setting.value if setting and setting.value not in (None, "") else default


def set_setting_value(key, value):
    setting = AppSetting.query.filter_by(key=key).first()
    if not setting:
        setting = AppSetting(key=key, value=str(value))
        db.session.add(setting)
    else:
        setting.value = str(value)
    return setting


def safe_int_setting(key, default, minimum=None):
    try:
        value = int(setting_value(key, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def is_secret_setting_key(key):
    """True für Zugangsdaten, die nie in Export-/Backup-ZIPs landen sollen.

    Wichtig: Passwort-Regeln wie password_min_length sind keine Geheimnisse und bleiben erhalten.
    """
    key = (key or "").lower()
    non_secret_password_settings = {
        "password_min_length",
        "password_require_upper",
        "password_require_lower",
        "password_require_digit",
        "password_require_special",
    }
    if key in non_secret_password_settings:
        return False

    exact_secret_keys = {
        "mail_password",
        "backup_webdav_password",
        "dropbox_token",
        "dropbox_access_token",
        "dropbox_refresh_token",
        "onedrive_token",
        "onedrive_access_token",
        "onedrive_refresh_token",
        "google_drive_token",
        "google_drive_access_token",
        "google_drive_refresh_token",
        "paypal_api_key",
        "paypal_api_secret",
        "paypal_client_secret",
        "paypal_access_token",
    }
    if key in exact_secret_keys:
        return True

    secret_fragments = (
        "_password",
        "password_",
        "_secret",
        "secret_",
        "_token",
        "token_",
        "access_token",
        "refresh_token",
        "api_key",
        "client_secret",
    )
    return any(fragment in key for fragment in secret_fragments)


def sanitized_app_settings_dict():
    """App-Einstellungen für Export/Backup ohne geheime Zugangsdaten."""
    data = {}
    for row in AppSetting.query.order_by(AppSetting.key).all():
        data[row.key] = "" if is_secret_setting_key(row.key) else row.value
    return data


def get_app_setting(key, default=None):
    setting = AppSetting.query.filter_by(key=key).first()
    if setting is None or setting.value is None:
        return default
    return setting.value
