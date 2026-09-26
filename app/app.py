from datetime import date, datetime, timedelta
import uuid
import hashlib
import os
import json
import zipfile
import shutil
import sqlite3
import zlib
import struct
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING, ROUND_FLOOR
import calendar
import re
import secrets
import smtplib
import csv
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from io import StringIO, BytesIO
from email.message import EmailMessage
from sqlalchemy import or_

from flask import render_template, redirect, url_for, request, flash, jsonify, send_file, Response, abort, after_this_request
from flask_login import (
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from auth import create_initial_admin, role_required
from config import (
    Config,
    TEST_DATABASE_PROFILES,
    get_active_database_profile,
    get_database_path,
    get_document_dir,
    normalize_database_profile,
    set_active_database_profile,
)
from models import (
    db,
    User,
    Member,
    RateSetting,
    GuestPenaltyPolicy,
    AccountTransaction,
    BowlingEvent,
    EventParticipant,
    PenaltyType,
    ParticipantPenalty,
    MemberPenaltyTransaction,
    CashbookEntry,
    EventEditLock,
    MonthlyContributionBatch,
    MonthlyContributionPayment,
    AppSetting,
    AuditLog,
    CashAudit,
    AnnualClosing,
    InterestSetting,
    InterestBooking,
    PasswordResetToken,
    Document,
)
from extensions import app, login_manager, APP_VERSION, RATE_TYPES, BASE_RATE_KEYS

# Datensicherung: gemeinsames DeveloperKit-Modul statt der bisherigen
# alleinstehenden Implementierung (siehe routes/backups.py, die jetzt nur
# noch den Kegelkasse-spezifischen Vereins-Export/-Import enthält). Nutzt
# Kegelkasses eigene db-Instanz (siehe developerkit.backup-Docstring: Flask-
# SQLAlchemy erlaubt pro App nur eine einzige SQLAlchemy-Instanz).
from developerkit.backup import init_app as init_backup

init_backup(
    app,
    db=db,
    backup_dir=os.getenv("BACKUP_DIR", "/app/backups"),
    documents_dir=str(get_document_dir()),
    app_label="Kegelkasse",
    base_template="base.html",
)


@app.context_processor
def inject_backup_db_info():
    # Nur für die Datensicherungs-Übersicht berechnen (SQLite-weite
    # Tabellen-/Zeilenzählung ist nicht ganz billig) - das eigene
    # Kegelkasse-Template dort (templates/backup/uebersicht.html, überschreibt
    # das generische DeveloperKit-Template) zeigt diese Werte im "Backup
    # erstellen"-Bereich an, wie vor der Umstellung auf developerkit.backup.
    if request.endpoint != "developerkit_backup.uebersicht":
        return {}
    from developerkit.backup.service import human_file_size

    db_path = get_database_path()
    table_count = 0
    row_count = 0
    db_size = 0
    if db_path.exists():
        db_size = db_path.stat().st_size
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            tables = [
                row[0]
                for row in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            table_count = len(tables)
            for table in tables:
                try:
                    row_count += int(cursor.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0)
                except Exception:
                    pass
            conn.close()
        except Exception:
            pass
    return {
        "db_info": {
            "db_size_label": human_file_size(db_size),
            "table_count": table_count,
            "row_count": row_count,
        }
    }


def active_database_info():
    profile = get_active_database_profile()
    info = TEST_DATABASE_PROFILES.get(profile, TEST_DATABASE_PROFILES["production"]).copy()
    info["key"] = profile
    info["database_path"] = str(get_database_path(profile))
    info["documents_path"] = str(get_document_dir(profile))
    return info


def database_profile_display(profile=None):
    """Kurzlabel für die Kopfzeile. Produktiv nutzt den Vereinsnamen, Tests bleiben technisch klar."""
    profile = normalize_database_profile(profile or get_active_database_profile())
    info = TEST_DATABASE_PROFILES.get(profile, TEST_DATABASE_PROFILES["production"]).copy()
    if profile == "production":
        try:
            label = setting_value("club_name", "Alle 8te") or "Alle 8te"
        except Exception:
            label = "Alle 8te"
        description = "Produktivdatenbank"
    else:
        label = info.get("label", profile)
        description = "Testdatenbank"
    return {
        "key": profile,
        "label": label,
        "description": description,
        "is_test": bool(info.get("is_test")),
    }


def database_profile_display_list():
    return [database_profile_display(key) for key in TEST_DATABASE_PROFILES.keys()]


@app.context_processor
def inject_database_test_mode():
    active_profile = get_active_database_profile()
    return {
        "active_database_profile": active_profile,
        "active_database_info": active_database_info(),
        "database_profile_displays": database_profile_display_list(),
        "database_profiles": TEST_DATABASE_PROFILES,
        "app_version": APP_VERSION,
    }

@app.context_processor
def inject_branding():
    logo_path = branding_logo_path()
    return {
        "has_logo": logo_path is not None,
        "logo_filename": logo_path.name if logo_path else None,
        "logo_display_mode": setting_value("logo_display_mode", "none"),
    }


@app.context_processor
def inject_active_event_navigation():
    try:
        active_event_nav = get_active_event()
    except Exception:
        active_event_nav = None

    return {
        "active_event_nav": active_event_nav,
    }


@app.context_processor
def inject_donate_settings():
    return {
        "donate_button_enabled": setting_value("donate_button_enabled", "1") == "1",
    }


@app.context_processor
def inject_update_check():
    if not (current_user.is_authenticated and current_user.role == "admin"):
        return {"update_available": False}
    try:
        from services.update_check import check_for_update
        update_available, latest_version, latest_release_url = check_for_update(APP_VERSION)
    except Exception:
        update_available, latest_version, latest_release_url = False, None, None
    return {
        "update_available": update_available,
        "latest_version": latest_version,
        "latest_release_url": latest_release_url,
    }


@app.context_processor
def inject_nav_badges():
    """Kleine Erledigungs-Zähler für das Menü (z. B. offene Monatsbeiträge)."""
    try:
        if not (current_user.is_authenticated and current_user.role in ("admin", "cashier", "auditor")):
            return {"nav_open_monthly_count": 0}
        nav_open_monthly_count = MonthlyContributionBatch.query.filter(MonthlyContributionBatch.status != "finalized").count()
    except Exception:
        nav_open_monthly_count = 0

    return {
        "nav_open_monthly_count": nav_open_monthly_count,
    }

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def installation_needs_setup():
    """Öffentliche-Version-Basis: Bei leerer Benutzerliste zuerst den Einrichtungsassistenten anzeigen."""
    try:
        return User.query.count() == 0
    except Exception:
        return False


@app.before_request
def redirect_to_first_start_setup():
    if request.endpoint in ("static", "first_start_setup", "login"):
        return
    if installation_needs_setup():
        return redirect(url_for("first_start_setup"))


from services.money import euro_to_cents, form_euro_to_cents, cents_to_euro, round_tax_cents, round_to_ten_cents

# -----------------------------------------------------------------------------
# Export-Helfer
# -----------------------------------------------------------------------------

from services.export import (
    export_filename,
    export_rows_to_csv,
    export_rows_to_excel,
    export_rows_to_pdf,
    export_response,
    export_headers,
)


from services.pdf import (
    BRANDING_DIR,
    _PDF_PAGE_WIDTH,
    _PDF_PAGE_HEIGHT,
    pdf_escape,
    _pdf_text_cmd,
    decode_png_image,
    jpeg_dimensions_and_components,
    build_logo_pdf_image,
    branding_logo_path,
    get_logo_pdf_image,
    _pdf_page_header_cmds,
    _pdf_footer_cmds,
    _pdf_assemble,
)


import routes.cashbook  # noqa: E402,F401  registriert /cashbook*
from routes.cashbook import account_balance, cashbook_export_rows, cashbook_category_options

import routes.search  # noqa: E402,F401  registriert /search

import routes.penalty_balances  # noqa: E402,F401  registriert /penalty-balances
from routes.penalty_balances import member_penalty_balance

import routes.reports  # noqa: E402,F401  registriert /reports, /exports*, /audit-log
from routes.reports import report_member_name, build_count_stat_rows, build_absence_stat_rows, competition_rank, export_year_options

import routes.annual_closings  # noqa: E402,F401  registriert /annual-closings*
from routes.annual_closings import closing_for_year, closed_year_block_message, annual_report_figures, annual_report_export_row, build_annual_report_pdf


from services.settings import (
    setting_value,
    set_setting_value,
    safe_int_setting,
    is_secret_setting_key,
    sanitized_app_settings_dict,
    get_app_setting,
)
from services.audit import audit_log, audit_value

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/app/backups"))
ACTIVE_DATABASE_PROFILE = get_active_database_profile()
DOCUMENT_DIR = get_document_dir(ACTIVE_DATABASE_PROFILE)
ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "gif", "webp", "doc", "docx", "xls", "xlsx", "txt", "csv", "json", "html", "htm", "rtf"}

DEFAULT_DOCUMENT_CATEGORIES = ["Bahnrechnungen", "Kassenprüfung", "Jahresabschluss", "Kegeltour", "Historische Importdaten", "Vereinsunterlagen", "Sonstiges"]
DATABASE_PATH = get_database_path(ACTIVE_DATABASE_PROFILE)


from services.password import (
    password_policy,
    password_policy_text,
    validate_password_policy,
    create_password_reset_token,
    active_password_reset_tokens,
)
from services.mail import mail_settings, mail_settings_summary, send_system_mail
from services.monthly_contribution_reminder import maybe_send_cashier_reminder


def username_is_available(username, current_user_id=None):
    query = User.query.filter(db.func.lower(User.username) == username.lower())
    if current_user_id:
        query = query.filter(User.id != current_user_id)
    return query.first() is None




from services.dates import (
    first_weekday_of_month,
    add_month,
    last_day_of_month,
    nth_weekday_of_month,
    last_weekday_of_month,
    _month_label,
    _last_n_months,
    parse_month_param,
    month_label,
    first_day_of_month,
    shift_weekend_to_monday,
)


from routes.events import next_event_date_from_rhythm, next_event_label, get_active_event


def current_rate_cents(key, target_date):
    rate = (
        RateSetting.query
        .filter(RateSetting.key == key)
        .filter(RateSetting.valid_from <= target_date)
        .order_by(RateSetting.valid_from.desc(), RateSetting.id.desc())
        .first()
    )

    return rate.amount_cents if rate else 0


def guest_penalties_charged(target_date):
    """True = Gast zahlt neben der Gastgebühr auch die Strafen des Abends.
    Ohne gepflegte Regel gilt True (bisheriges, fest einprogrammiertes Verhalten)."""
    policy = (
        GuestPenaltyPolicy.query
        .filter(GuestPenaltyPolicy.valid_from <= target_date)
        .order_by(GuestPenaltyPolicy.valid_from.desc(), GuestPenaltyPolicy.id.desc())
        .first()
    )
    return policy.charge_penalties if policy else True


def create_default_rates():
    defaults = {
        "monthly_fee": 2000,
        "guest_fee": 500,
        "absence_excused": 250,
        "absence_unexcused": 250,
        "lane_cost_default": 1850,
        "penalty_pump": 20,
        "penalty_wreath": 20,
        "penalty_all_nine": 20,
        "penalty_lost_game": 20,
    }

    default_date = datetime.today().date()

    for key, amount in defaults.items():
        exists = RateSetting.query.filter_by(key=key).first()

        if not exists:
            rate = RateSetting(
                key=key,
                label=RATE_TYPES[key],
                amount_cents=amount,
                valid_from=default_date,
                active=True,
                note="Automatisch angelegter Startwert",
            )
            db.session.add(rate)

    if not GuestPenaltyPolicy.query.first():
        db.session.add(GuestPenaltyPolicy(
            charge_penalties=True,
            valid_from=default_date,
            note="Automatisch angelegter Startwert (bisheriges Verhalten beibehalten)",
        ))

    db.session.commit()


from services.schema_migrations import (
    ensure_column,
    record_schema_migration,
    migrate_schema_extensions,
    migrate_viewer_roles_to_member,
    ensure_interest_booking_cancel_columns,
)


def create_default_penalty_types():
    """Legt die ersten Strafarten an, falls noch keine vorhanden sind."""
    defaults = [
        ("penalty_pump", "Pumpe", "count", current_rate_cents("penalty_pump", datetime.today().date()) or 20, 10, "self"),
        ("penalty_wreath", "Kranz", "count", current_rate_cents("penalty_wreath", datetime.today().date()) or 20, 20, "others"),
        ("penalty_all_nine", "Alle Neune", "count", current_rate_cents("penalty_all_nine", datetime.today().date()) or 20, 30, "others"),
        ("penalty_lost_game", "Verlorenes Spiel", "count", current_rate_cents("penalty_lost_game", datetime.today().date()) or 20, 40, "self"),
        ("manual_other", "Sonstige Strafe", "amount", 0, 90, "self"),
    ]

    for key, name, kind, amount_cents, sort_order, target_mode in defaults:
        exists = PenaltyType.query.filter_by(key=key).first()
        if not exists:
            db.session.add(PenaltyType(
                key=key,
                name=name,
                kind=kind,
                amount_cents=amount_cents,
                active=True,
                sort_order=sort_order,
                target_mode=target_mode,
            ))

    # Sinnvolle Voreinstellung für bestehende Installationen:
    # Kranz, Alle Neune/Alle Neue belasten alle anderen Spieler.
    for key in ("penalty_wreath", "penalty_all_nine"):
        penalty_type = PenaltyType.query.filter_by(key=key).first()
        if penalty_type and getattr(penalty_type, "target_mode", "self") == "self":
            penalty_type.target_mode = "others"

    # Robust für importierte/umbenannte Strafarten ohne passenden key.
    for penalty_type in PenaltyType.query.all():
        normalized_name = (penalty_type.name or "").strip().casefold()
        if (
            "kranz" in normalized_name
            or "alle neune" in normalized_name
            or "alle neue" in normalized_name
            or "alle neun" in normalized_name
        ):
            penalty_type.target_mode = "others"

    db.session.commit()


def migrate_old_participant_fields_to_dynamic_penalties():
    """Übernimmt alte fest codierte Straf-Felder in die neue flexible Struktur.

    Die alten Spalten bleiben erstmal im Modell, damit vorhandene Daten nicht verloren gehen.
    Diese Migration legt nur fehlende dynamische Einträge nach.
    """
    mappings = [
        ("penalty_pump", "pumps"),
        ("penalty_wreath", "wreaths"),
        ("penalty_all_nine", "all_nines"),
        ("penalty_lost_game", "lost_games"),
    ]

    for key, old_field in mappings:
        penalty_type = PenaltyType.query.filter_by(key=key).first()
        if not penalty_type:
            continue

        participants = EventParticipant.query.all()
        for participant in participants:
            old_value = getattr(participant, old_field, 0) or 0
            if old_value <= 0:
                continue

            exists = ParticipantPenalty.query.filter_by(
                participant_id=participant.id,
                penalty_type_id=penalty_type.id,
            ).first()

            if not exists:
                db.session.add(ParticipantPenalty(
                    participant_id=participant.id,
                    penalty_type_id=penalty_type.id,
                    quantity=old_value,
                    amount_cents=0,
                ))

    other_type = PenaltyType.query.filter_by(key="manual_other").first()
    if other_type:
        participants = EventParticipant.query.all()
        for participant in participants:
            old_amount = participant.manual_penalty_cents or 0
            old_note = participant.manual_penalty_note
            if old_amount <= 0 and not old_note:
                continue

            exists = ParticipantPenalty.query.filter_by(
                participant_id=participant.id,
                penalty_type_id=other_type.id,
            ).first()

            if not exists:
                db.session.add(ParticipantPenalty(
                    participant_id=participant.id,
                    penalty_type_id=other_type.id,
                    quantity=1 if old_amount else 0,
                    amount_cents=old_amount,
                    note=old_note,
                ))

    db.session.commit()


from services.validation import (
    clean_username,
    validate_email_format,
    iban_is_valid,
    bic_is_valid,
    paypal_link_is_valid,
)


@app.route("/setup", methods=["GET", "POST"])
def first_start_setup():
    """Startbildschirm für neue/leere Installationen.

    Neue Nutzer wählen zuerst den passenden Einstieg: neu anlegen, Backup
    wiederherstellen, Daten importieren oder Demo starten. Die konkreten
    Formulare erscheinen erst nach der Auswahl.
    """
    from developerkit.backup.service import backup_dir, verify_backup_file, restore_database_from_backup

    if not installation_needs_setup():
        return redirect(url_for("login"))

    mode = request.args.get("mode", "start")

    if request.method == "POST":
        setup_action = (request.form.get("setup_action") or "create_club").strip()

        if setup_action == "choose_create":
            return redirect(url_for("first_start_setup", mode="create"))

        if setup_action == "choose_restore":
            return redirect(url_for("first_start_setup", mode="restore"))

        if setup_action == "choose_import":
            return redirect(url_for("first_start_setup", mode="import"))

        if setup_action == "choose_demo":
            return redirect(url_for("first_start_setup", mode="demo"))

        if setup_action == "start_demo":
            set_active_database_profile("demo")
            if not get_database_path("demo").exists():
                try:
                    reset_or_delete_test_database("demo", mode="reset")
                except Exception:
                    pass
            restart_kegelkasse_process()
            return """
            <!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><meta http-equiv=\"refresh\" content=\"5;url=/login\"><title>Demo wird gestartet</title><style>body{font-family:Arial,sans-serif;background:#f5f7fb;color:#18212f;padding:30px}.box{max-width:620px;margin:60px auto;background:white;border:1px solid #e5e7eb;border-radius:16px;padding:24px;box-shadow:0 10px 30px rgba(15,23,42,.07)}</style></head><body><div class=\"box\"><h1>Demo-Datenbank wird geöffnet …</h1><p>Die Kegelkasse startet kurz neu. Danach kannst du dich mit den Demo-Zugangsdaten anmelden.</p><p>Falls nichts passiert: <a href=\"/login\">Loginseite öffnen</a>.</p></div></body></html>
            """

        if setup_action == "restore_backup":
            uploaded = request.files.get("backup_file")
            if not uploaded or not uploaded.filename:
                flash("Bitte eine Backup-ZIP auswählen.", "danger")
                return render_template("setup_wizard.html", setup_mode="restore")
            if not uploaded.filename.lower().endswith(".zip"):
                flash("Bitte eine Kegelkasse-Backup-ZIP auswählen.", "danger")
                return render_template("setup_wizard.html", setup_mode="restore")

            backup_dir().mkdir(parents=True, exist_ok=True)
            safe_name = secure_filename(uploaded.filename) or "restore.zip"
            upload_path = backup_dir() / f"setup_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
            uploaded.save(upload_path)
            try:
                verify_result = verify_backup_file(upload_path)
                # verify_backup_file liefert in älteren Ständen einen zusammenfassenden Text,
                # in neueren Ständen ggf. ein Dict. Beides ist gültig, solange keine Exception
                # geworfen wurde. Wichtig: Restore darf nicht auf einem String .get() aufrufen.
                if isinstance(verify_result, dict) and not verify_result.get("database", False):
                    raise ValueError("Die Backup-Datenbank ist nicht lesbar.")
                restore_before = restore_database_from_backup(upload_path)
                flash(f"Datensicherung wurde wiederhergestellt. Vorher wurde automatisch gesichert: {restore_before.name if restore_before else 'nicht erstellt'}", "success")
                restart_kegelkasse_process()
                return """
                <!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><meta http-equiv=\"refresh\" content=\"5;url=/login\"><title>Wiederherstellung läuft</title><style>body{font-family:Arial,sans-serif;background:#f5f7fb;color:#18212f;padding:30px}.box{max-width:620px;margin:60px auto;background:white;border:1px solid #e5e7eb;border-radius:16px;padding:24px;box-shadow:0 10px 30px rgba(15,23,42,.07)}</style></head><body><div class=\"box\"><h1>Wiederherstellung abgeschlossen …</h1><p>Die Kegelkasse startet kurz neu. Danach bitte neu anmelden.</p><p>Falls nichts passiert: <a href=\"/login\">Loginseite öffnen</a>.</p></div></body></html>
                """
            except Exception as exc:
                db.session.rollback()
                flash(f"Datensicherung konnte nicht wiederhergestellt werden: {exc}", "danger")
                return render_template("setup_wizard.html", setup_mode="restore")

        if setup_action == "create_club":
            club_name = (request.form.get("club_name") or "").strip()
            username = clean_username(request.form.get("username") or "")
            email = (request.form.get("email") or "").strip() or None
            password = request.form.get("password") or ""
            password_repeat = request.form.get("password_repeat") or ""

            account_holder = (request.form.get("account_holder") or "").strip()
            iban = (request.form.get("iban") or "").strip().upper()
            bic = (request.form.get("bic") or "").strip().upper()
            primary_color = (request.form.get("primary_color") or "").strip()
            secondary_color = (request.form.get("secondary_color") or "").strip()

            mail_host = (request.form.get("mail_host") or "").strip()
            mail_port = (request.form.get("mail_port") or "587").strip() or "587"
            mail_username = (request.form.get("mail_username") or "").strip()
            mail_password = request.form.get("mail_password") or ""
            mail_from_email = (request.form.get("mail_from_email") or "").strip()
            mail_from_name = (request.form.get("mail_from_name") or "Kegelkasse").strip()
            mail_use_tls = "1" if request.form.get("mail_use_tls", "1") else "0"

            errors = []
            if not club_name:
                errors.append("Bitte einen Vereins-/Clubnamen angeben.")
            if not username:
                errors.append("Bitte einen Admin-Benutzernamen angeben.")
            if not email:
                errors.append("Bitte eine E-Mail-Adresse für den Admin angeben.")
            elif not validate_email_format(email):
                errors.append("Bitte eine gültige E-Mail-Adresse für den Admin angeben.")
            if password != password_repeat:
                errors.append("Die Passwörter stimmen nicht überein.")
            password_errors = validate_password_policy(password)
            if password_errors:
                errors.append("Das Passwort erfüllt die Vorgaben nicht: " + ", ".join(password_errors) + ".")
            if account_holder and len(account_holder) < 3:
                errors.append("Der Kontoinhaber sollte mindestens 3 Zeichen haben.")
            if iban and not iban_is_valid(iban):
                errors.append("Die IBAN ist formal ungültig oder hat eine falsche Prüfziffer.")
            if bic and not bic_is_valid(bic):
                errors.append("Die BIC hat kein gültiges Format.")
            if mail_port:
                try:
                    mail_port_int = int(mail_port)
                    if mail_port_int <= 0 or mail_port_int > 65535:
                        raise ValueError()
                except ValueError:
                    errors.append("Der SMTP-Port muss eine gültige Portnummer sein.")
                    mail_port_int = 587
            else:
                mail_port_int = 587
            if mail_from_email and not validate_email_format(mail_from_email):
                errors.append("Die Absenderadresse für E-Mail muss gültig sein.")
            if mail_host and not mail_from_email:
                errors.append("Wenn SMTP eingerichtet wird, sollte auch eine Absenderadresse angegeben werden.")

            if errors:
                for error in errors:
                    flash(error, "danger")
                return render_template("setup_wizard.html", setup_mode="create", setup_errors=errors)

            admin = User(
                username=username,
                email=email,
                role="admin",
                active=True,
                is_system_user=True,
                password_hash=generate_password_hash(password),
            )
            db.session.add(admin)
            db.session.flush()

            set_setting_value("club_name", club_name)
            optional_settings = {
                "club_account_holder": account_holder,
                "club_iban": iban,
                "club_bic": bic,
                "club_primary_color": primary_color,
                "club_secondary_color": secondary_color,
                "mail_enabled": "1" if mail_host and mail_from_email else "0",
                "mail_host": mail_host,
                "mail_port": str(mail_port_int),
                "mail_username": mail_username,
                "mail_from_email": mail_from_email,
                "mail_from_name": mail_from_name,
                "mail_use_tls": mail_use_tls,
                "setup_completed_at": datetime.now().isoformat(timespec="seconds"),
            }
            for key, value in optional_settings.items():
                value = (value or "").strip()
                if value:
                    set_setting_value(key, value)
            if mail_password:
                set_setting_value("mail_password", mail_password)

            audit_log(
                "system",
                "first_start_setup_completed",
                "Ersteinrichtung abgeschlossen",
                details="Admin-Systembenutzer ohne Kegelmitgliedschaft angelegt. Optionale Vereins-, Bank- und E-Mail-Daten wurden gespeichert, soweit angegeben.",
                object_type="User",
                object_id=admin.id,
                new_value=f"Verein: {audit_value(club_name)}; Admin: {audit_value(username)}",
            )
            db.session.commit()
            login_user(admin)
            flash("Ersteinrichtung abgeschlossen. Du bist als Admin angemeldet.", "success")
            return redirect(url_for("dashboard"))

    return render_template("setup_wizard.html", setup_mode=mode)


DASHBOARD_CARD_DEFINITIONS = [
    ("next_event", "Aktiver/Nächster Kegelabend"),
    ("tasks", "Heute zu erledigen"),
    ("member_stats", "Meine Statistik (nur für Mitglieder sichtbar)"),
    ("cash_balance", "Kassenstand"),
    ("members_overview", "Mitglieder-Übersicht"),
    ("year_overview", "Dieses Jahr"),
    ("top_rankings", "Top-Wertungen"),
    ("cash_audit", "Kassenprüfung"),
    ("annual_closing", "Jahresabschluss"),
    ("monthly_contributions", "Offene Monatsbeiträge"),
]


def dashboard_card_visibility():
    return {key: setting_value(f"dashboard_card_{key}", "1") == "1" for key, _ in DASHBOARD_CARD_DEFINITIONS}


@app.route("/")
@login_required
def dashboard():
    today = datetime.today().date()
    member_count = Member.query.count()
    active_members = Member.query.filter_by(active=True).order_by(Member.first_name.asc(), Member.last_name.asc()).all()
    active_member_count = len(active_members)
    next_date = next_event_date_from_rhythm()

    active_event = get_active_event()

    cash_balance_cents = account_balance("cash")
    bank_balance_cents = account_balance("bank")
    open_penalties_cents = 0
    credit_cents = 0
    open_member_rows = []

    for member in active_members:
        balance = member_penalty_balance(member.id)
        if balance > 0:
            open_penalties_cents += balance
            open_member_rows.append({
                "name": report_member_name(member),
                "amount_cents": balance,
                "amount_euro": cents_to_euro(balance),
            })
        elif balance < 0:
            credit_cents += abs(balance)

    last_closed_event = (
        BowlingEvent.query
        .filter(BowlingEvent.status == "closed")
        .order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc())
        .first()
    )
    last_cash_audit = CashAudit.query.order_by(CashAudit.audit_date.desc(), CashAudit.id.desc()).first()
    last_closing = AnnualClosing.query.order_by(AnnualClosing.year.desc()).first()
    last_audit_entries = AuditLog.query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(5).all()
    open_monthly_batches = MonthlyContributionBatch.query.filter(MonthlyContributionBatch.status != "finalized").order_by(MonthlyContributionBatch.year.desc(), MonthlyContributionBatch.month.desc()).all()

    current_year = today.year
    year_start = datetime(current_year, 1, 1).date()
    year_end = datetime(current_year + 1, 1, 1).date()
    year_closed_events = BowlingEvent.query.filter(
        BowlingEvent.status == "closed",
        BowlingEvent.event_date >= year_start,
        BowlingEvent.event_date < year_end,
    ).count()
    year_cancelled_events = BowlingEvent.query.filter(
        BowlingEvent.status == "cancelled",
        BowlingEvent.event_date >= year_start,
        BowlingEvent.event_date < year_end,
    ).count()

    year_cashbook_entries = CashbookEntry.query.filter(
        CashbookEntry.is_void == False,  # noqa: E712
        CashbookEntry.booking_date >= year_start,
        CashbookEntry.booking_date < year_end,
    ).all()
    year_income_cents = sum(entry.amount_cents or 0 for entry in year_cashbook_entries if entry.direction == "income")
    year_expense_cents = sum(entry.amount_cents or 0 for entry in year_cashbook_entries if entry.direction == "expense")

    pump_rows = build_count_stat_rows(current_year, "penalty_pump")
    wreath_rows = build_count_stat_rows(current_year, "penalty_wreath")
    absence_rows = build_absence_stat_rows(current_year)

    top_pump = pump_rows[0] if pump_rows else None
    top_wreath = wreath_rows[0] if wreath_rows else None
    top_absence = absence_rows[0] if absence_rows else None
    member_dashboard = None
    current_member = Member.query.filter_by(user_id=current_user.id).first()

    if current_member:
        member_balance_cents = member_penalty_balance(current_member.id)

        member_pumps = next((row["total"] for row in pump_rows if row["member_id"] == current_member.id), 0)
        member_wreaths = next((row["total"] for row in wreath_rows if row["member_id"] == current_member.id), 0)

        member_absence_row = next((row for row in absence_rows if row.get("member_id") == current_member.id), None)
        member_absences = member_absence_row["total"] if member_absence_row else 0

        pump_rank = competition_rank(pump_rows, current_member.id)
        absence_rank = competition_rank(absence_rows, current_member.id)

        member_dashboard = {
            "name": report_member_name(current_member),
            "open_penalties": cents_to_euro(member_balance_cents) if member_balance_cents > 0 else None,
            "credit": cents_to_euro(abs(member_balance_cents)) if member_balance_cents < 0 else None,
            "pumps": member_pumps,
            "wreaths": member_wreaths,
            "absences": member_absences,
            "pump_rank": pump_rank,
            "absence_rank": absence_rank,
            "club_total": cents_to_euro(cash_balance_cents + bank_balance_cents),
        }

    open_member_count = len(open_member_rows)
    open_member_rows = sorted(open_member_rows, key=lambda row: row["amount_cents"], reverse=True)[:5]

    dashboard_tasks = []
    show_admin_tasks = current_user.role in ("admin", "cashier")

    if show_admin_tasks and active_event:
        if active_event.status == "open":
            status_text = "Strafen erfassen"
        elif active_event.status == "settlement":
            status_text = "Barzahlungen erfassen"
        elif active_event.status == "lane_cost":
            status_text = "Bahnkosten erfassen"
        else:
            status_text = "In Bearbeitung"

        dashboard_tasks.append({
            "priority": "high",
            "icon": "🎳",
            "title": "Aktiven Kegelabend fortsetzen",
            "description": f"{active_event.event_date.strftime('%d.%m.%Y')} · {status_text}",
            "url": url_for("event_detail", event_id=active_event.id),
        })

    if show_admin_tasks and open_monthly_batches:
        batch = open_monthly_batches[0]
        dashboard_tasks.append({
            "priority": "medium",
            "icon": "📌",
            "title": "Monatsbeiträge prüfen",
            "description": f"{batch.period_label()} · {batch.status_label()}",
            "url": url_for("monthly_contributions"),
        })
    elif show_admin_tasks:
        # Für einen fälligen Monat existiert unter Umständen noch gar kein
        # Batch-Datensatz, solange niemand die Monatsbeiträge-Seite gespeichert
        # hat (der Batch wird dort lazy angelegt). Ohne diesen Zweig bliebe der
        # Hinweis unsichtbar, obwohl die Grundgebühren noch offen sind.
        last_finalized_batch = (
            MonthlyContributionBatch.query
            .filter(MonthlyContributionBatch.status == "finalized")
            .order_by(MonthlyContributionBatch.year.desc(), MonthlyContributionBatch.month.desc())
            .first()
        )
        if last_finalized_batch:
            expected_year = last_finalized_batch.year
            expected_month = last_finalized_batch.month + 1
            if expected_month > 12:
                expected_month = 1
                expected_year += 1
        else:
            expected_year, expected_month = today.year, today.month

        # Hinweis erscheint erst ab dem 5. Tag des auf den Beitragszeitraum
        # folgenden Monats - unabhängig davon, wie viele Monate der Rückstand
        # inzwischen umfasst.
        reminder_year, reminder_month = expected_year, expected_month + 1
        if reminder_month > 12:
            reminder_month = 1
            reminder_year += 1
        monthly_contribution_task_due = today >= date(reminder_year, reminder_month, 5)

        if monthly_contribution_task_due:
            maybe_send_cashier_reminder(expected_year, expected_month)
            dashboard_tasks.append({
                "priority": "medium",
                "icon": "📌",
                "title": "Monatsbeiträge prüfen",
                "description": f"{month_label(expected_year, expected_month)} · noch nicht begonnen",
                "url": url_for("monthly_contributions"),
            })

    if show_admin_tasks and open_penalties_cents > 0:
        dashboard_tasks.append({
            "priority": "medium",
            "icon": "💶",
            "title": "Offene Strafkonten prüfen",
            "description": f"{open_member_count} Mitglieder · {cents_to_euro(open_penalties_cents)} € offen",
            "url": url_for("penalty_balances"),
        })

    if show_admin_tasks:
        days_since_audit = (today - last_cash_audit.audit_date).days if last_cash_audit and last_cash_audit.audit_date else None
        if days_since_audit is None or days_since_audit > 90:
            dashboard_tasks.append({
                "priority": "medium" if days_since_audit is not None else "low",
                "icon": "🔎",
                "title": "Kassenprüfung fällig",
                "description": (
                    f"Letzte Prüfung vor {days_since_audit} Tagen ({last_cash_audit.audit_date.strftime('%d.%m.%Y')})"
                    if days_since_audit is not None
                    else "Noch keine Kassenprüfung erfasst"
                ),
                "url": url_for("cash_audits"),
            })

    return render_template(
        "dashboard.html",
        member_count=member_count,
        active_member_count=active_member_count,
        cash_balance=cents_to_euro(cash_balance_cents),
        bank_balance=cents_to_euro(bank_balance_cents),
        total_balance=cents_to_euro(cash_balance_cents + bank_balance_cents),
        total_with_open=cents_to_euro(cash_balance_cents + bank_balance_cents + open_penalties_cents),
        open_penalties=cents_to_euro(open_penalties_cents),
        member_credits=cents_to_euro(credit_cents),
        next_event_date=next_date,
        next_event_label=next_event_label(next_date),
        active_event=active_event,
        active_event_label=next_event_label(active_event.event_date) if active_event else None,
        last_closed_event=last_closed_event,
        last_cash_audit=last_cash_audit,
        last_closing=last_closing,
        last_audit_entries=last_audit_entries,
        open_monthly_batches=open_monthly_batches,
        open_member_rows=open_member_rows,
        dashboard_tasks=dashboard_tasks,
        current_year=current_year,
        year_closed_events=year_closed_events,
        year_cancelled_events=year_cancelled_events,
        year_income=cents_to_euro(year_income_cents),
        year_expense=cents_to_euro(year_expense_cents),
        top_pump=top_pump,
        top_wreath=top_wreath,
        top_absence=top_absence,
        member_dashboard=member_dashboard,
        dashboard_cards=dashboard_card_visibility(),
    )




def restart_kegelkasse_process():
    """Containerprozess neu starten, damit SQLAlchemy sicher auf die gewählte DB zeigt."""
    def restart_process():
        os._exit(0)
    threading.Timer(1.0, restart_process).start()


def reset_or_delete_test_database(profile, mode="delete"):
    """Setzt eine Testdatenbank zurück oder löscht sie komplett.

    production wird hier bewusst nie verändert. Für die Demo-Datenbank wird, falls
    vorhanden, eine mitgelieferte Demo-Basis kopiert. Leere Testdatenbanken werden
    gelöscht und beim nächsten Start wieder frisch angelegt.
    """
    profile = normalize_database_profile(profile)
    info = TEST_DATABASE_PROFILES.get(profile, {})
    if not info.get("is_test"):
        raise ValueError("Die echte Vereinsdatenbank kann hier nicht geändert werden.")

    test_db_path = get_database_path(profile)
    test_doc_dir = get_document_dir(profile)

    if test_db_path.exists():
        test_db_path.unlink()
    if test_doc_dir.exists():
        shutil.rmtree(test_doc_dir)

    # Demo auf definierten Ausgangsstand zurücksetzen, wenn Demo-Basis im Image liegt.
    if profile == "demo" and mode == "reset":
        seed_dir = Path(app.root_path) / "demo_seed"
        seed_db = seed_dir / "kegelkasse_demo_seed.db"
        seed_documents = seed_dir / "documents"
        if seed_db.exists():
            test_db_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed_db, test_db_path)
        if seed_documents.exists():
            test_doc_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(seed_documents, test_doc_dir)

# Die frühere Testdatenbank-Umschaltung (/test-database, versteckt hinter dem
# Logo bzw. dem Testmodus-Banner) wurde am 2026-09-17 komplett entfernt: seit
# Produktiv (Port 8091) und Demo (Port 8092) als getrennte Container laufen,
# war sie nur noch eine unnötige, potenziell riskante Angriffsfläche.
# Die Demo-/Testdatenbank ist jetzt ausschließlich über den eigenen Port
# 8092 erreichbar, ein Umschalten zwischen den Profilen innerhalb eines
# laufenden Containers ist nicht mehr vorgesehen. reset_or_delete_test_database()
# bleibt erhalten, da sie weiterhin vom Einrichtungsassistenten (first_start_setup,
# "Demo ausprobieren") für neue Installationen genutzt wird.


# Einfacher Brute-Force-Schutz beim Login: pro Benutzername gezählt, nur im
# Arbeitsspeicher (kein Schema-Update nötig, setzt sich beim Neustart des
# Prozesses zurück - für diesen niedrigfrequentierten Vereinseinsatz
# ausreichend). Sperrt nur kurz, damit sich niemand versehentlich selbst
# lange aussperrt.
_LOGIN_FAILED_ATTEMPTS = {}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_LOCKOUT_SECONDS = 60


def _login_locked_out(username):
    now = time.time()
    attempts = [t for t in _LOGIN_FAILED_ATTEMPTS.get(username, []) if now - t < _LOGIN_WINDOW_SECONDS]
    _LOGIN_FAILED_ATTEMPTS[username] = attempts
    return len(attempts) >= _LOGIN_MAX_ATTEMPTS and (now - attempts[-_LOGIN_MAX_ATTEMPTS]) < _LOGIN_LOCKOUT_SECONDS


def _login_record_failed_attempt(username):
    _LOGIN_FAILED_ATTEMPTS.setdefault(username, []).append(time.time())


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if username and _login_locked_out(username):
            flash("Zu viele Fehlversuche. Bitte kurz warten und es erneut versuchen.", "danger")
            return render_template("login.html")

        user = User.query.filter_by(username=username, active=True).first()

        if user and check_password_hash(user.password_hash, password):
            _LOGIN_FAILED_ATTEMPTS.pop(username, None)
            login_user(user)
            return redirect(url_for("dashboard"))

        if username:
            _login_record_failed_attempt(username)
        flash("Benutzername oder Passwort ist falsch.", "danger")

    return render_template("login.html")


@app.route("/password-forgot", methods=["GET", "POST"])
def password_forgot():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        lookup = request.form.get("lookup", "").strip()
        user = None
        if lookup:
            user = User.query.filter(
                User.active.is_(True),
                or_(User.username == lookup, User.email == lookup),
            ).first()

        if user:
            reset = create_password_reset_token(user)
            reset_url = url_for("password_reset", token=reset.token, _external=True)
            mail_sent, mail_message = send_system_mail(
                user.email,
                "Passwort zurücksetzen – Kegelkasse",
                "Hallo,\n\nfür dein Kegelkasse-Konto wurde ein Passwort-Reset angefordert.\n\n"
                f"Einmal-Link: {reset_url}\n\n"
                "Der Link ist 24 Stunden gültig und kann nur einmal benutzt werden.\n\n"
                "Falls du das nicht angefordert hast, ignoriere diese Nachricht.\n",
            )
            audit_log(
                "system",
                "password_reset_requested",
                f"Passwort-Reset für {user.username} angefordert",
                details=(
                    "Einmal-Link wurde erzeugt. "
                    + ("E-Mail wurde versendet." if mail_sent else f"E-Mail nicht versendet: {mail_message}")
                ),
                object_type="User",
                object_id=user.id,
            )
            db.session.commit()

        flash("Falls ein passendes aktives Konto gefunden wurde, wurde eine Passwort-Zurücksetzung vorbereitet.", "success")
        return redirect(url_for("login"))

    return render_template("password_forgot.html")


@app.route("/password-reset/<token>", methods=["GET", "POST"])
def password_reset(token):
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    reset = PasswordResetToken.query.filter_by(token=token).first_or_404()
    if reset.used_at or reset.expires_at < datetime.utcnow() or not reset.user or not reset.user.active:
        flash("Dieser Passwort-Link ist abgelaufen oder wurde bereits verwendet.", "danger")
        return redirect(url_for("login"))

    policy_text = password_policy_text()

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        new_password_repeat = request.form.get("new_password_repeat", "")

        if new_password != new_password_repeat:
            flash("Die neuen Passwörter stimmen nicht überein.", "danger")
            return redirect(url_for("password_reset", token=token))

        password_errors = validate_password_policy(new_password)
        if password_errors:
            flash("Das neue Passwort erfüllt die Vorgaben nicht: " + ", ".join(password_errors) + ".", "danger")
            return redirect(url_for("password_reset", token=token))

        reset.user.password_hash = generate_password_hash(new_password)
        reset.used_at = datetime.utcnow()
        audit_log(
            "system",
            "password_reset_completed",
            f"Passwort für {reset.user.username} per Einmal-Link geändert",
            details="Passwort wurde über Passwort-Reset-Link neu gesetzt.",
            object_type="User",
            object_id=reset.user.id,
        )
        db.session.commit()
        flash("Passwort wurde geändert. Du kannst dich jetzt anmelden.", "success")
        return redirect(url_for("login"))

    return render_template("password_reset.html", reset=reset, password_policy_text=policy_text)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    policy_text = password_policy_text()

    if request.method == "POST":
        username = clean_username(request.form.get("username", ""))
        email = request.form.get("email", "").strip() or None
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        new_password_repeat = request.form.get("new_password_repeat", "")

        changed_parts = []

        old_username = current_user.username
        old_email = current_user.email

        if not username:
            flash("Der Benutzername darf nicht leer sein.", "danger")
            return redirect(url_for("account"))

        if username != old_username:
            if not username_is_available(username, current_user.id):
                flash("Dieser Benutzername ist bereits vergeben.", "danger")
                return redirect(url_for("account"))
            current_user.username = username
            changed_parts.append("Benutzername geändert")

        if email != old_email:
            if not validate_email_format(email):
                flash("Bitte eine gültige E-Mail-Adresse eingeben.", "danger")
                return redirect(url_for("account"))
            current_user.email = email
            changed_parts.append("E-Mail-Adresse geändert")

        if new_password or new_password_repeat or current_password:
            if not check_password_hash(current_user.password_hash, current_password):
                flash("Das aktuelle Passwort ist nicht korrekt.", "danger")
                return redirect(url_for("account"))

            if new_password != new_password_repeat:
                flash("Die neuen Passwörter stimmen nicht überein.", "danger")
                return redirect(url_for("account"))

            password_errors = validate_password_policy(new_password)
            if password_errors:
                flash("Das neue Passwort erfüllt die Vorgaben nicht: " + ", ".join(password_errors) + ".", "danger")
                return redirect(url_for("account"))

            current_user.password_hash = generate_password_hash(new_password)
            changed_parts.append("Passwort geändert")

        if changed_parts:
            audit_log(
                "system",
                "account_updated",
                f"Eigenes Konto {current_user.username} geändert",
                details="; ".join(changed_parts),
                object_type="User",
                object_id=current_user.id,
                old_value=f"Benutzername: {audit_value(old_username)}; E-Mail: {audit_value(old_email)}",
                new_value=f"Benutzername: {audit_value(current_user.username)}; E-Mail: {audit_value(current_user.email)}",
            )
            db.session.commit()
            flash("Dein Konto wurde gespeichert.", "success")
        else:
            flash("Es wurden keine Änderungen erkannt.", "success")

        return redirect(url_for("account"))

    return render_template("account.html", password_policy_text=policy_text)


import routes.admin  # noqa: E402,F401  registriert /admin, /branding/logo


import routes.documents  # noqa: E402,F401  registriert /documents*
from routes.documents import document_allowed


import routes.backups  # noqa: E402,F401  registriert /import-export, /club/export/download


import routes.members  # noqa: E402,F401  registriert /members*


import routes.finance_settings  # noqa: E402,F401  registriert /settings/rates*, /settings/event-rhythm, /settings/penalty-types*


import routes.monthly_contributions  # noqa: E402,F401  registriert /finance/monthly-bank-closing, /finance/monthly-contributions


import routes.events  # noqa: E402,F401  registriert /events*, /my-event*, /my-stats


def migrate_backup_settings_to_developerkit():
    """Einmalige Migration: Kegelkasses bisherige Backup-Einstellungen
    (eigene AppSetting-Tabelle) in die neue developerkit.backup-Settings-
    Tabelle übernehmen, damit ein bereits konfiguriertes Cloud-Ziel oder
    eine Verschlüsselung nach der Umstellung nicht verloren geht. Läuft nur
    einmal (Markierung in der neuen Tabelle)."""
    from developerkit.backup.models import setting as dk_setting, set_setting as dk_set_setting

    if dk_setting("_migrated_from_kegelkasse_v1"):
        return

    keys_to_migrate = [
        "backup_enabled", "backup_interval", "backup_time",
        "backup_keep_days", "backup_keep_count", "backup_include_documents",
        "backup_extra_target_enabled", "backup_target_type",
        "backup_webdav_url", "backup_webdav_username", "backup_webdav_password",
        "backup_dropbox_app_key", "backup_dropbox_app_secret",
        "dropbox_refresh_token", "dropbox_access_token", "dropbox_token_expires_at", "dropbox_account_label",
        "backup_google_client_id", "backup_google_client_secret",
        "google_drive_refresh_token", "google_drive_access_token", "google_drive_token_expires_at", "google_drive_account_label",
        "backup_onedrive_client_id", "backup_onedrive_client_secret",
        "onedrive_refresh_token", "onedrive_access_token", "onedrive_token_expires_at", "onedrive_account_label",
        "backup_last_auto", "backup_last_result",
        "backup_encryption_mode", "backup_encryption_public_key",
        "backup_encryption_public_key_created_at", "backup_encryption_password",
    ]
    for key in keys_to_migrate:
        alt = AppSetting.query.filter_by(key=key).first()
        if alt and alt.value not in (None, ""):
            dk_set_setting(key, alt.value)

    dk_set_setting("_migrated_from_kegelkasse_v1", "1")
    db.session.commit()


with app.app_context():
    db.create_all()
    ensure_interest_booking_cancel_columns()
    migrate_schema_extensions()
    migrate_backup_settings_to_developerkit()

    # Öffentliche-Version-Basis: Bei einer leeren Installation wird kein versteckter
    # Standard-Admin mehr erzeugt. Stattdessen führt /setup durch die Ersteinrichtung.
    if os.getenv("KEGELKASSE_AUTO_CREATE_ADMIN", "0") == "1":
        create_initial_admin(
            app.config["ADMIN_USERNAME"],
            app.config["ADMIN_PASSWORD"],
        )

    create_default_rates()
    migrate_viewer_roles_to_member()
    create_default_penalty_types()
    migrate_old_participant_fields_to_dynamic_penalties()


if __name__ == "__main__":
    # Bewusst kein app.run() hier: direktes "python app.py" würde app.py unter
    # dem Modulnamen __main__ statt "app" laden. Jeder spätere "from app import
    # X" innerhalb eines Request-Handlers (Standardmuster in den routes/*.py-
    # Dateien) würde app.py dann beim ersten Aufruf ein zweites Mal komplett
    # neu ausführen und abstürzen (siehe v0.99.6-Bugfix). Start immer über
    # run.py, das app.py sauber als Modul importiert.
    raise SystemExit(
        "Bitte nicht 'python app.py' direkt starten - das reproduziert einen "
        "bereits behobenen Absturz-Bug. Stattdessen 'python run.py' verwenden "
        "(im Docker-Image bereits als Standard-Kommando eingerichtet)."
    )
