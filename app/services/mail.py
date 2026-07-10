"""SMTP-Mailversand für System-E-Mails (z. B. Passwort-Reset)."""
import smtplib
from email.message import EmailMessage

from services.settings import setting_value


def mail_settings():
    return {
        "enabled": setting_value("mail_enabled", "0") == "1",
        "host": setting_value("mail_host", ""),
        "port": int(setting_value("mail_port", "587") or 587),
        "username": setting_value("mail_username", ""),
        "password": setting_value("mail_password", ""),
        "from_email": setting_value("mail_from_email", ""),
        "from_name": setting_value("mail_from_name", "Kegelkasse"),
        "use_tls": setting_value("mail_use_tls", "1") == "1",
    }


def mail_settings_summary(settings=None):
    settings = settings or mail_settings()
    status = "aktiv" if settings["enabled"] else "inaktiv"
    tls = "TLS/STARTTLS" if settings["use_tls"] else "ohne TLS"
    sender = settings["from_email"] or "-"
    host = settings["host"] or "-"
    return f"Status: {status}; Server: {host}:{settings['port']} ({tls}); Absender: {sender}"


def send_system_mail(to_email, subject, body):
    settings = mail_settings()
    if not settings["enabled"]:
        return False, "Mailversand ist deaktiviert."
    if not to_email:
        return False, "Beim Benutzer ist keine E-Mail-Adresse hinterlegt."
    if not settings["host"] or not settings["from_email"]:
        return False, "SMTP-Server oder Absender-Adresse fehlt."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings['from_name']} <{settings['from_email']}>" if settings["from_name"] else settings["from_email"]
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings["host"], settings["port"], timeout=15) as smtp:
            if settings["use_tls"]:
                smtp.starttls()
            if settings["username"]:
                smtp.login(settings["username"], settings["password"])
            smtp.send_message(msg)
        return True, "E-Mail wurde versendet."
    except Exception as exc:
        return False, f"E-Mail konnte nicht versendet werden: {exc}"
