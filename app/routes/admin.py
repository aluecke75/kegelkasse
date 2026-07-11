"""Administrationsbereich: Benutzerverwaltung, Passwort-Policy, Dashboard-Karten,
Vereinslogo, Mail-Einstellungen, Systembenutzer."""
from datetime import datetime

from flask import request, flash, redirect, url_for, render_template, send_file, abort
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash

from extensions import app
from auth import role_required
from models import db, User, PasswordResetToken
from services.settings import setting_value, set_setting_value
from services.audit import audit_log, audit_value
from services.validation import clean_username
from services.password import password_policy, password_policy_text, validate_password_policy, active_password_reset_tokens
from services.mail import mail_settings, mail_settings_summary, send_system_mail
from services.pdf import (
    BRANDING_DIR,
    branding_logo_path,
    build_logo_pdf_image,
)

ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg"}
_LOGO_MAGIC_BYTES = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
}


@app.route("/admin", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_area():
    from app import DASHBOARD_CARD_DEFINITIONS, dashboard_card_visibility, username_is_available

    if request.method == "POST":
        form_action = request.form.get("form_action", "user")

        if form_action == "cancel_password_reset":
            reset_id = int(request.form.get("reset_id", 0) or 0)
            reset = PasswordResetToken.query.get_or_404(reset_id)
            reset.used_at = datetime.utcnow()
            audit_log(
                "system",
                "password_reset_cancelled",
                f"Passwort-Reset für {reset.user.username if reset.user else 'unbekannt'} ungültig gemacht",
                details="Offener Passwort-Reset-Link wurde im Adminbereich deaktiviert.",
                object_type="PasswordResetToken",
                object_id=reset.id,
            )
            db.session.commit()
            flash("Passwort-Reset-Link wurde ungültig gemacht.", "success")
            return redirect(url_for("admin_area"))

        if form_action == "password_policy":
            old_policy = password_policy_text()
            min_length_raw = request.form.get("password_min_length", "8").strip()
            try:
                min_length = max(1, int(min_length_raw))
            except ValueError:
                flash("Die Mindestlänge muss eine ganze Zahl sein.", "danger")
                return redirect(url_for("admin_area"))

            set_setting_value("password_min_length", min_length)
            set_setting_value("password_require_upper", "1" if request.form.get("password_require_upper") else "0")
            set_setting_value("password_require_lower", "1" if request.form.get("password_require_lower") else "0")
            set_setting_value("password_require_digit", "1" if request.form.get("password_require_digit") else "0")
            set_setting_value("password_require_special", "1" if request.form.get("password_require_special") else "0")
            new_policy = password_policy_text()

            audit_log(
                "settings",
                "password_policy_updated",
                "Passwortvorgaben geändert",
                details="Passwortvorgaben aktualisiert.",
                object_type="AppSetting",
                old_value=old_policy,
                new_value=new_policy,
            )
            db.session.commit()
            flash("Passwortvorgaben wurden gespeichert.", "success")
            return redirect(url_for("admin_area"))

        if form_action == "dashboard_cards":
            def summary():
                return ", ".join(
                    f"{label}: {'an' if setting_value(f'dashboard_card_{key}', '1') == '1' else 'aus'}"
                    for key, label in DASHBOARD_CARD_DEFINITIONS
                )
            old_summary = summary()
            for key, _label in DASHBOARD_CARD_DEFINITIONS:
                set_setting_value(f"dashboard_card_{key}", "1" if request.form.get(f"dashboard_card_{key}") else "0")
            new_summary = summary()
            audit_log(
                "settings",
                "dashboard_cards_updated",
                "Dashboard-Kartenauswahl geändert",
                details="Sichtbare Dashboard-Karten aktualisiert.",
                object_type="AppSetting",
                old_value=old_summary,
                new_value=new_summary,
            )
            db.session.commit()
            flash("Dashboard-Einstellungen wurden gespeichert.", "success")
            return redirect(url_for("admin_area"))

        if form_action == "event_settings":
            old_value = setting_value("event_closed_confirmation_enabled", "0")
            new_value = "1" if request.form.get("event_closed_confirmation_enabled") else "0"
            set_setting_value("event_closed_confirmation_enabled", new_value)
            audit_log(
                "settings",
                "event_settings_updated",
                "Kegelabend-Ablauf-Einstellungen geändert",
                details="Abschluss-Bestätigungsseite nach Bahnkosten-Erfassung.",
                object_type="AppSetting",
                old_value="an" if old_value == "1" else "aus",
                new_value="an" if new_value == "1" else "aus",
            )
            db.session.commit()
            flash("Einstellungen wurden gespeichert.", "success")
            return redirect(url_for("admin_area"))

        if form_action == "logo_upload":
            file = request.files.get("logo_file")
            if not file or not file.filename:
                flash("Bitte eine Bilddatei für das Vereinslogo auswählen.", "danger")
                return redirect(url_for("admin_area"))

            ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
            if ext not in ALLOWED_LOGO_EXTENSIONS:
                flash("Bitte eine PNG- oder JPG-Datei für das Logo hochladen.", "danger")
                return redirect(url_for("admin_area"))

            file_bytes = file.read()
            if len(file_bytes) > 3 * 1024 * 1024:
                flash("Die Bilddatei ist zu groß (maximal 3 MB).", "danger")
                return redirect(url_for("admin_area"))

            magic_ok = any(file_bytes.startswith(sig) for sig in _LOGO_MAGIC_BYTES.get(ext, ()))
            if not magic_ok:
                flash("Die Datei sieht nicht wie ein gültiges Bild aus. Bitte eine echte PNG/JPG-Datei hochladen.", "danger")
                return redirect(url_for("admin_area"))

            BRANDING_DIR.mkdir(parents=True, exist_ok=True)
            for old_ext in ("png", "jpg", "jpeg"):
                old_file = BRANDING_DIR / f"logo.{old_ext}"
                if old_file.exists():
                    old_file.unlink()

            stored_name = f"logo.{ext}"
            (BRANDING_DIR / stored_name).write_bytes(file_bytes)

            try:
                test_image = build_logo_pdf_image(BRANDING_DIR / stored_name)
            except Exception:
                test_image = None
            if not test_image:
                (BRANDING_DIR / stored_name).unlink(missing_ok=True)
                flash("Diese Bilddatei konnte nicht gelesen werden. Unterstützt werden 8-Bit-PNG (RGB/RGBA, nicht interlaced) und JPEG.", "danger")
                return redirect(url_for("admin_area"))

            audit_log(
                "settings",
                "logo_uploaded",
                "Vereinslogo hochgeladen",
                details=f"Datei: {file.filename}; Format: {ext}; Größe: {len(file_bytes)} Bytes",
                object_type="Branding",
            )
            db.session.commit()
            flash("Vereinslogo wurde gespeichert und erscheint ab sofort in PDF-Exporten.", "success")
            return redirect(url_for("admin_area"))

        if form_action == "logo_remove":
            removed = False
            for ext in ("png", "jpg", "jpeg"):
                old_file = BRANDING_DIR / f"logo.{ext}"
                if old_file.exists():
                    old_file.unlink()
                    removed = True
            if removed:
                audit_log(
                    "settings",
                    "logo_removed",
                    "Vereinslogo entfernt",
                    object_type="Branding",
                )
                db.session.commit()
                flash("Vereinslogo wurde entfernt.", "success")
            return redirect(url_for("admin_area"))

        if form_action == "logo_display_settings":
            new_mode = request.form.get("logo_display_mode", "none")
            if new_mode not in ("none", "header", "header_and_login"):
                new_mode = "none"
            old_mode = setting_value("logo_display_mode", "none")
            set_setting_value("logo_display_mode", new_mode)
            audit_log(
                "settings",
                "logo_display_settings_updated",
                "Logo-Anzeige geändert",
                object_type="AppSetting",
                old_value=old_mode,
                new_value=new_mode,
            )
            db.session.commit()
            flash("Einstellungen wurden gespeichert.", "success")
            return redirect(url_for("admin_area"))

        if form_action == "create_system_user":
            username = clean_username(request.form.get("new_username", ""))
            email = request.form.get("new_email", "").strip() or None
            role = request.form.get("new_role", "admin").strip()
            password = request.form.get("new_password", "")
            if role not in ("admin", "cashier", "auditor"):
                role = "admin"
            if not username:
                flash("Bitte einen Benutzernamen für den Systembenutzer angeben.", "danger")
                return redirect(url_for("admin_area"))
            if not username_is_available(username):
                flash("Dieser Benutzername ist bereits vergeben.", "danger")
                return redirect(url_for("admin_area"))
            if not password:
                flash("Bitte ein Startpasswort für den Systembenutzer angeben.", "danger")
                return redirect(url_for("admin_area"))
            password_errors = validate_password_policy(password)
            if password_errors:
                flash("Das Passwort erfüllt die Vorgaben nicht: " + ", ".join(password_errors) + ".", "danger")
                return redirect(url_for("admin_area"))

            user = User(
                username=username,
                email=email,
                role=role,
                active=True,
                is_system_user=True,
                password_hash=generate_password_hash(password),
            )
            db.session.add(user)
            db.session.flush()
            audit_log(
                "system",
                "system_user_created",
                f"Systembenutzer {user.username} angelegt",
                details="Reines Benutzerkonto ohne Kegelmitgliedschaft. Erscheint nicht in Kegelabenden, Monatsbeiträgen, Strafkonten oder Statistiken.",
                object_type="User",
                object_id=user.id,
                new_value=f"Benutzername: {audit_value(user.username)}; Rolle: {audit_value(user.role_label())}; E-Mail: {audit_value(user.email)}",
            )
            db.session.commit()
            flash("Systembenutzer wurde angelegt. Er nimmt nicht am Kegelbetrieb teil.", "success")
            return redirect(url_for("admin_area"))

        if form_action == "mail_settings":
            old_summary = mail_settings_summary()
            set_setting_value("mail_enabled", "1" if request.form.get("mail_enabled") else "0")
            set_setting_value("mail_host", request.form.get("mail_host", "").strip())
            try:
                mail_port = int(request.form.get("mail_port", "587") or 587)
            except ValueError:
                flash("Der SMTP-Port muss eine Zahl sein.", "danger")
                return redirect(url_for("admin_area"))
            set_setting_value("mail_port", mail_port)
            set_setting_value("mail_username", request.form.get("mail_username", "").strip())
            mail_password = request.form.get("mail_password", "")
            if mail_password:
                set_setting_value("mail_password", mail_password)
            set_setting_value("mail_from_email", request.form.get("mail_from_email", "").strip())
            set_setting_value("mail_from_name", request.form.get("mail_from_name", "Kegelkasse").strip())
            set_setting_value("mail_use_tls", "1" if request.form.get("mail_use_tls") else "0")
            new_summary = mail_settings_summary()
            audit_log(
                "settings",
                "mail_settings_updated",
                "E-Mail-Einstellungen geändert",
                details="SMTP-/Mailversand-Einstellungen wurden aktualisiert. Das Passwort wird im Protokoll bewusst nicht angezeigt.",
                object_type="AppSetting",
                old_value=old_summary,
                new_value=new_summary,
            )
            db.session.commit()
            flash("E-Mail-Einstellungen wurden gespeichert.", "success")
            return redirect(url_for("admin_area"))

        if form_action == "test_mail":
            test_to = request.form.get("test_to", "").strip() or current_user.email
            sent, message = send_system_mail(
                test_to,
                "Testmail – Kegelkasse",
                "Hallo,\n\ndas ist eine Testmail aus der Kegelkasse.\n\nWenn diese Mail ankommt, ist der Mailversand grundsätzlich eingerichtet.\n",
            )
            audit_log(
                "system",
                "test_mail_sent" if sent else "test_mail_failed",
                "Testmail versendet" if sent else "Testmail fehlgeschlagen",
                details=message,
                object_type="AppSetting",
            )
            db.session.commit()
            flash(message, "success" if sent else "danger")
            return redirect(url_for("admin_area"))

        user_id = int(request.form.get("user_id", 0) or 0)
        user = User.query.get_or_404(user_id)

        old_username = user.username
        old_email = user.email
        username = clean_username(request.form.get("username", ""))
        if not username:
            flash("Der Benutzername darf nicht leer sein.", "danger")
            return redirect(url_for("admin_area"))
        if username != old_username and not username_is_available(username, user.id):
            flash("Dieser Benutzername ist bereits vergeben.", "danger")
            return redirect(url_for("admin_area"))

        user.username = username
        user.email = request.form.get("email", "").strip() or None
        password = request.form.get("password", "")
        password_changed = False
        if password:
            password_errors = validate_password_policy(password)
            if password_errors:
                flash("Das neue Passwort erfüllt die Vorgaben nicht: " + ", ".join(password_errors) + ".", "danger")
                return redirect(url_for("admin_area"))
            user.password_hash = generate_password_hash(password)
            password_changed = True

        details = []
        if old_username != user.username:
            details.append("Benutzername geändert")
        if old_email != user.email:
            details.append("E-Mail-Adresse geändert")
        if password_changed:
            details.append("Passwort geändert")
        if not details:
            details.append("Keine inhaltliche Änderung erkannt")

        audit_log(
            "system",
            "user_saved",
            f"Benutzer {user.username} gespeichert",
            details="; ".join(details),
            object_type="User",
            object_id=user.id,
            old_value=f"Benutzername: {audit_value(old_username)}; E-Mail: {audit_value(old_email)}",
            new_value=f"Benutzername: {audit_value(user.username)}; E-Mail: {audit_value(user.email)}",
        )
        db.session.commit()
        flash("Benutzer wurde gespeichert.", "success")
        return redirect(url_for("admin_area"))

    users = User.query.order_by(User.username).all()
    logo_path = branding_logo_path()
    return render_template(
        "admin.html",
        users=users,
        password_policy=password_policy(),
        password_policy_text=password_policy_text(),
        reset_tokens=active_password_reset_tokens(),
        mail_settings=mail_settings(),
        mail_settings_summary=mail_settings_summary(),
        has_logo=logo_path is not None,
        logo_filename=logo_path.name if logo_path else None,
        dashboard_card_definitions=DASHBOARD_CARD_DEFINITIONS,
        dashboard_cards=dashboard_card_visibility(),
        event_closed_confirmation_enabled=setting_value("event_closed_confirmation_enabled", "0") == "1",
    )


@app.route("/branding/logo")
def branding_logo():
    path = branding_logo_path()
    if not path:
        abort(404)
    mimetype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return send_file(path, mimetype=mimetype)
