from datetime import date, datetime, timedelta
import uuid
import hashlib
import os
import json
import zipfile
import shutil
import sqlite3
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import calendar
import re
import secrets
import smtplib
import csv
import threading
import urllib.parse
import urllib.request
import urllib.error
from io import StringIO, BytesIO
from email.message import EmailMessage
from sqlalchemy import or_

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, send_file, Response, abort, after_this_request
from flask_login import (
    LoginManager,
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


app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Bitte zuerst anmelden."
login_manager.init_app(app)

APP_VERSION = "0.98.38"


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
        "active_database_display": database_profile_display(active_profile),
        "database_profile_displays": database_profile_display_list(),
        "database_profiles": TEST_DATABASE_PROFILES,
        "developer_mode": app.config.get("DEVELOPER_MODE", False),
        "app_version": APP_VERSION,
    }

@app.context_processor
def inject_active_event_navigation():
    try:
        active_event_nav = (
            BowlingEvent.query
            .filter(BowlingEvent.status.in_(("open", "settlement", "lane_cost")))
            .order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc())
            .first()
        )
    except Exception:
        active_event_nav = None

    return {
        "active_event_nav": active_event_nav,
    }

@app.context_processor
def inject_active_event_navigation():
    try:
        active_event_nav = (
            BowlingEvent.query
            .filter(BowlingEvent.status.in_(("open", "settlement", "lane_cost")))
            .order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc())
            .first()
        )
    except Exception:
        active_event_nav = None

    return {
        "active_event_nav": active_event_nav,
    }

RATE_TYPES = {
    "monthly_fee": "Monatsbeitrag",
    "guest_fee": "Gastgebühr",
    "absence_excused": "Fehlen entschuldigt",
    "absence_unexcused": "Fehlen unentschuldigt",
    "lane_cost_default": "Bahnkosten Standard",
    "penalty_pump": "Pumpe",
    "penalty_wreath": "Kranz",
    "penalty_all_nine": "Alle Neune",
    "penalty_lost_game": "Verlorenes Spiel",
}

# Grundeinstellungen ohne Strafarten. Strafarten werden separat im Modul "Strafarten" gepflegt.
BASE_RATE_KEYS = [
    "monthly_fee",
    "guest_fee",
    "absence_excused",
    "absence_unexcused",
    "lane_cost_default",
]



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
    if request.endpoint in ("static", "first_start_setup", "login", "switch_database_profile", "test_database_control"):
        return
    if installation_needs_setup():
        return redirect(url_for("first_start_setup"))


@app.before_request
def maybe_create_scheduled_backup():
    # Sehr leichte eingebaute Automatik: läuft beim ersten App-Aufruf nach der geplanten Uhrzeit.
    if request.endpoint in ("static", "download_backup"):
        return
    try:
        settings = backup_settings()
        if not settings["enabled"]:
            return
        now = datetime.now()
        try:
            scheduled_time = datetime.strptime(settings["time"], "%H:%M").time()
        except ValueError:
            scheduled_time = datetime.strptime("02:00", "%H:%M").time()
        if now.time() < scheduled_time:
            return

        last_value = settings.get("last_auto") or ""
        last_date = None
        if last_value:
            try:
                last_date = datetime.fromisoformat(last_value).date()
            except ValueError:
                last_date = None

        due = False
        if last_date is None:
            due = True
        elif settings["interval"] == "daily":
            due = now.date() > last_date
        elif settings["interval"] == "weekly":
            due = (now.date() - last_date).days >= 7
        elif settings["interval"] == "monthly":
            due = (now.year, now.month) != (last_date.year, last_date.month)

        if due:
            backup = create_database_backup(kind="auto")
            set_setting_value("backup_last_auto", now.isoformat(timespec="seconds"))
            cleanup_old_backups()
            audit_log(
                "Datensicherung",
                "auto_backup_created",
                "Automatische Sicherung erstellt",
                details=f"Automatische Sicherung erstellt: {backup.name}",
                object_type="Backup",
                new_value=backup.name,
            )
            db.session.commit()
    except Exception:
        db.session.rollback()


def euro_to_cents(value):
    """Wandelt Euro-Eingaben robust in Cent um.

    Unterstützt deutsche Schreibweise mit Tausenderpunkt und Cent-Komma:
    5 -> 5,00 €, 3,7 -> 3,70 €, 1.234,56 -> 1.234,56 €.
    Zur Sicherheit akzeptieren wir auch alte Punkt-Dezimalwerte wie 3.70.
    """
    if value is None:
        return 0

    raw = str(value).strip().replace("€", "").replace(" ", "")

    if raw == "":
        return 0

    # Komfort für mobile Eingabe: ,5 oder .5 bedeutet 0,50 €.
    if raw.startswith((",", ".")):
        raw = "0" + raw

    if not re.fullmatch(r"\d+(?:[.,]\d+)*", raw):
        raise ValueError("Ungültiger Euro-Betrag")

    if "," in raw:
        # Deutsche Schreibweise: Punkte sind Tausendertrenner, Komma trennt Cent.
        cleaned = raw.replace(".", "").replace(",", ".")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts) == 2 and len(parts[1]) <= 2:
            # Alte/technische Schreibweise: 3.70 = 3,70
            cleaned = raw
        elif len(parts[-1]) == 3 and all(len(part) == 3 for part in parts[1:]):
            # 1.234 oder 1.234.567 = Tausenderpunkte ohne Cent.
            cleaned = raw.replace(".", "")
        else:
            raise ValueError("Ungültiger Euro-Betrag")
    else:
        cleaned = raw

    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        raise ValueError("Ungültiger Euro-Betrag")

    return int(round(float(cleaned) * 100))


def form_euro_to_cents(field_name, label):
    try:
        return euro_to_cents(request.form.get(field_name, "0"))
    except ValueError:
        flash(f"Bitte bei '{label}' nur Zahlen eingeben, z. B. 5 oder 3,70.", "danger")
        raise


def cents_to_euro(value):
    amount = (value or 0) / 100
    formatted = f"{amount:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")




# -----------------------------------------------------------------------------
# Export-Helfer
# -----------------------------------------------------------------------------

def export_filename(prefix, extension):
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    safe_prefix = secure_filename(prefix) or "export"
    return f"{safe_prefix}_{stamp}.{extension}"


def export_filter_year():
    raw = request.args.get("year", "all")
    if raw and raw != "all":
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def export_year_options():
    years = set(report_year_options())
    for closing in AnnualClosing.query.all():
        if closing.year:
            years.add(closing.year)
    for entry in CashbookEntry.query.all():
        if entry.booking_date:
            years.add(entry.booking_date.year)
    return sorted(years, reverse=True)


def export_rows_to_csv(rows, headers, filename):
    output = StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row.get(header, "") for header in headers])
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def export_rows_to_excel(rows, headers, filename, title="Kegelkasse Export"):
    # Ohne zusätzliche Python-Abhängigkeit: Excel-kompatibles HTML mit .xls-Endung.
    def esc(value):
        return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    html = [
        '<html><head><meta charset="utf-8"></head><body>',
        f'<h2>{esc(title)}</h2>',
        '<table border="1" cellspacing="0" cellpadding="4">',
        '<tr>' + ''.join(f'<th>{esc(header)}</th>' for header in headers) + '</tr>',
    ]
    for row in rows:
        html.append('<tr>' + ''.join(f'<td>{esc(row.get(header, ""))}</td>' for header in headers) + '</tr>')
    html.append('</table></body></html>')
    return Response(
        "\ufeff" + "\n".join(html),
        mimetype="application/vnd.ms-excel; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def pdf_escape(text):
    """Escape text for a PDF string using Windows-1252/WinAnsi.

    Standard PDF Type1 fonts such as Helvetica do not understand UTF-8 directly.
    Encoding the text as cp1252 fixes German umlauts in common PDF viewers.
    """
    raw = str(text or "").encode("cp1252", errors="replace")
    out = bytearray()
    for b in raw:
        if b in (40, 41, 92):  # (, ), \
            out.append(92)
            out.append(b)
        elif b < 32 or b > 126:
            out.extend(f"\\{b:03o}".encode("ascii"))
        else:
            out.append(b)
    return out.decode("ascii")


def _pdf_text_cmd(x, y, text, size=10, font="F1"):
    return f"BT /{font} {size} Tf {x} {y} Td ({pdf_escape(text)}) Tj ET"


def export_rows_to_pdf(rows, headers, filename, title="Kegelkasse Export"):
    """Dependency-free, more structured PDF export.

    The PDF uses built-in Helvetica fonts and cp1252 text encoding so ä/ö/ü/ß
    are displayed correctly in normal PDF readers.
    """
    page_width = 595
    page_height = 842
    margin = 42
    y_start = 785
    line_height = 15
    page_bottom = 52
    now_text = datetime.now().strftime("%d.%m.%Y %H:%M")

    rows = rows or []
    objects = []
    page_ids = []

    def new_page(page_no):
        cmds = []
        # Header area
        cmds.append("0.94 0.97 0.95 rg")
        cmds.append(f"{margin} 748 {page_width - 2*margin} 54 re f")
        cmds.append("0 g")
        cmds.append(_pdf_text_cmd(margin + 14, 782, title, 16, "F2"))
        cmds.append(_pdf_text_cmd(margin + 14, 762, f"Erstellt am: {now_text}", 9, "F1"))
        cmds.append(_pdf_text_cmd(page_width - margin - 80, 762, f"Seite {page_no}", 9, "F1"))
        cmds.append("0.80 0.80 0.80 RG")
        cmds.append(f"{margin} 735 m {page_width - margin} 735 l S")
        return cmds, 718

    def add_page(cmds):
        stream = "\n".join(cmds).encode("latin-1", errors="replace")
        content_id = len(objects) + 5
        page_id = content_id + 1
        objects.append(f"{content_id} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream\nendobj\n")
        objects.append(f"{page_id} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>\nendobj\n".encode("ascii"))
        page_ids.append(page_id)

    page_no = 1
    cmds, y = new_page(page_no)

    if not rows:
        cmds.append(_pdf_text_cmd(margin, y, "Keine Daten vorhanden.", 11, "F1"))
    else:
        for idx, row in enumerate(rows, 1):
            needed = line_height * (len(headers) + 2) + 14
            if y - needed < page_bottom:
                add_page(cmds)
                page_no += 1
                cmds, y = new_page(page_no)

            # Record card background
            card_height = line_height * (len(headers) + 1) + 12
            cmds.append("0.98 0.98 0.98 rg")
            cmds.append(f"{margin} {y - card_height + 8} {page_width - 2*margin} {card_height} re f")
            cmds.append("0.82 0.82 0.82 RG")
            cmds.append(f"{margin} {y - card_height + 8} {page_width - 2*margin} {card_height} re S")
            cmds.append("0 g")
            cmds.append(_pdf_text_cmd(margin + 10, y, f"Eintrag {idx}", 11, "F2"))
            y -= line_height + 2

            for header in headers:
                value = str(row.get(header, ""))
                line = f"{header}: {value}"
                # wrap rough text length, PDF-safe and readable
                max_len = 92
                if len(line) <= max_len:
                    cmds.append(_pdf_text_cmd(margin + 16, y, line, 9, "F1"))
                    y -= line_height
                else:
                    first = True
                    while line:
                        part = line[:max_len]
                        line = line[max_len:]
                        prefix = "" if first else "   "
                        cmds.append(_pdf_text_cmd(margin + 16, y, prefix + part, 9, "F1"))
                        y -= line_height
                        first = False
            y -= 10

    add_page(cmds)

    pdf_objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        f"2 0 obj\n<< /Type /Pages /Kids [{' '.join(str(i) + ' 0 R' for i in page_ids)}] /Count {len(page_ids)} >>\nendobj\n".encode("ascii"),
        b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>\nendobj\n",
    ] + objects

    buffer = BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for obj in pdf_objects:
        offsets.append(buffer.tell())
        buffer.write(obj)
    xref_pos = buffer.tell()
    buffer.write(f"xref\n0 {len(pdf_objects)+1}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(f"trailer\n<< /Size {len(pdf_objects)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii"))

    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def export_response(rows, headers, export_type, fmt, title):
    if fmt == "csv":
        return export_rows_to_csv(rows, headers, export_filename(export_type, "csv"))
    if fmt == "excel":
        return export_rows_to_excel(rows, headers, export_filename(export_type, "xls"), title=title)
    if fmt == "pdf":
        return export_rows_to_pdf(rows, headers, export_filename(export_type, "pdf"), title=title)
    flash("Unbekanntes Exportformat.", "danger")
    return redirect(url_for("exports_page"))


def cashbook_export_rows(year=None, account="all"):
    query = CashbookEntry.query.filter(CashbookEntry.is_void == False)  # noqa: E712
    if year:
        query = query.filter(db.extract("year", CashbookEntry.booking_date) == year)
    if account in ("cash", "bank"):
        query = query.filter(CashbookEntry.account == account)
    entries = query.order_by(CashbookEntry.booking_date.desc(), CashbookEntry.id.desc()).all()
    rows = []
    for entry in entries:
        rows.append({
            "Datum": entry.booking_date.strftime("%d.%m.%Y") if entry.booking_date else "",
            "Art": entry.direction_label(),
            "Konto/Umbuchung": entry.account_label(),
            "Betrag": f"{entry.amount_euro()} €",
            "Kategorie": entry.category or "",
            "Empfänger/Einzahler": entry.person or "",
            "Grund": entry.reason or "",
            "Erfasst von": entry.created_by_user.username if entry.created_by_user else "",
            "Notiz": entry.note or "",
        })
    return rows


def members_export_rows(scope="active"):
    members = Member.query.order_by(Member.first_name.asc(), Member.last_name.asc()).all()
    rows = []
    for member in members:
        balance = member_penalty_balance(member.id)
        if scope == "active" and not member.active:
            continue
        if scope == "open" and balance <= 0:
            continue
        if scope == "credit" and balance >= 0:
            continue
        rows.append({
            "Name": member.display_name(),
            "Vorname": member.first_name or "",
            "Nachname": member.last_name or "",
            "Spitzname": member.nickname or "",
            "E-Mail": member.email or (member.user.email if member.user else ""),
            "Benutzername": member.user.username if member.user else "",
            "Rolle": member.user.role_label() if member.user else "Mitglied ohne Login",
            "Aktiv": "Ja" if member.active else "Nein",
            "Dauerauftragstag": member.monthly_value_day or "",
            "Offene Strafen": f"{cents_to_euro(balance)} €" if balance > 0 else "0,00 €",
            "Guthaben": f"{cents_to_euro(abs(balance))} €" if balance < 0 else "0,00 €",
        })
    return rows


def penalty_balance_export_rows():
    rows = []
    for member in Member.query.order_by(Member.first_name.asc(), Member.last_name.asc()).all():
        balance = member_penalty_balance(member.id)
        rows.append({
            "Mitglied": member.display_name(),
            "Offene Strafen": f"{cents_to_euro(balance)} €" if balance > 0 else "0,00 €",
            "Guthaben": f"{cents_to_euro(abs(balance))} €" if balance < 0 else "0,00 €",
            "Saldo": f"{cents_to_euro(balance)} €",
        })
    return rows


def annual_closing_export_rows(year=None):
    query = AnnualClosing.query
    if year:
        query = query.filter(AnnualClosing.year == year)
    closings = query.order_by(AnnualClosing.year.desc()).all()
    rows = []
    for closing in closings:
        snapshot = annual_closing_snapshot(closing)
        rows.append(snapshot)
    return rows


def statistics_export_rows(year=None):
    player_rows = build_player_overview_rows(year)
    rows = []
    for row in player_rows:
        rows.append({
            "Mitglied": row["name"],
            "Anwesend": row["attended"],
            "Fehlt entschuldigt": row["excused"],
            "Fehlt unentschuldigt": row["unexcused"],
            "Strafen gesamt": f"{row['penalty_total_euro']} €",
            "Höchste Einzelstrafe": f"{row['highest_single_euro']} €",
            "Eingezahlt": f"{row['paid_euro']} €",
            "Offen": f"{row['open_euro']} €",
            "Guthaben": f"{row['credit_euro']} €",
        })
    return rows


def export_headers(export_type):
    return {
        "cashbook": ["Datum", "Art", "Konto/Umbuchung", "Betrag", "Kategorie", "Empfänger/Einzahler", "Grund", "Erfasst von", "Notiz"],
        "members": ["Name", "Vorname", "Nachname", "Spitzname", "E-Mail", "Benutzername", "Rolle", "Aktiv", "Dauerauftragstag", "Offene Strafen", "Guthaben"],
        "penalty_balances": ["Mitglied", "Offene Strafen", "Guthaben", "Saldo"],
        "annual_closing": ["Jahr", "Abschlussdatum", "Barkasse", "Bank", "Gesamtbestand", "Offene Strafen", "Guthaben Mitglieder", "Einnahmen im Jahr", "Ausgaben im Jahr", "Kegelabende abgeschlossen", "Kegelabende ausgefallen", "Kegelabende offen", "Letzte Kassenprüfung", "Notiz"],
        "statistics": ["Mitglied", "Anwesend", "Fehlt entschuldigt", "Fehlt unentschuldigt", "Strafen gesamt", "Höchste Einzelstrafe", "Eingezahlt", "Offen", "Guthaben"],
    }.get(export_type, [])


def audit_value(value):
    if value is None:
        return "-"
    if value is True:
        return "Ja"
    if value is False:
        return "Nein"
    return str(value)


def audit_diff_lines(old_snapshot, new_snapshot):
    lines = []
    keys = list(old_snapshot.keys())
    for key in new_snapshot.keys():
        if key not in old_snapshot:
            keys.append(key)

    for key in keys:
        old = old_snapshot.get(key)
        new = new_snapshot.get(key)
        if audit_value(old) != audit_value(new):
            lines.append(f"{key}: vorher {audit_value(old)} → nachher {audit_value(new)}")

    if not lines:
        return "Keine inhaltliche Änderung erkannt."
    return "\n".join(lines)


_AUDIT_DIFF_LINE_RE = re.compile(r"^(.*?): vorher (.*) → nachher (.*)$")


def audit_diff_rows(details):
    """Liest aus dem Details-Text nur die geänderten Felder als (Feld, Vorher, Nachher) heraus."""
    if not details:
        return []
    rows = []
    for line in details.splitlines():
        match = _AUDIT_DIFF_LINE_RE.match(line)
        if match:
            rows.append((match.group(1), match.group(2), match.group(3)))
    return rows


app.jinja_env.filters["audit_diff_rows"] = audit_diff_rows


def member_audit_snapshot(member):
    return {
        "Vorname": member.first_name,
        "Nachname": member.last_name or "",
        "Spitzname": member.nickname or "",
        "E-Mail": member.email or "",
        "Aktiv": member.active,
        "Eintritt": member.joined_at.isoformat() if member.joined_at else "",
        "Austritt": member.left_at.isoformat() if member.left_at else "",
        "Notiz": member.note or "",
        "Benutzerkonto": member.user.username if member.user else "-",
    }


def penalty_type_audit_snapshot(penalty_type):
    return {
        "Name": penalty_type.name,
        "Typ": penalty_type.kind_label(),
        "Betrag": f"{cents_to_euro(penalty_type.amount_cents)} €",
        "Berechnung": penalty_type.target_mode_label(),
        "Aktiv": penalty_type.active,
        "Reihenfolge": penalty_type.sort_order,
    }


def event_status_label(status):
    labels = {
        "open": "Offen",
        "settlement": "Barzahlungen erfassen",
        "lane_cost": "Bahnkosten erfassen",
        "closed": "Abgeschlossen",
        "cancelled": "Ausgefallen",
        "present": "Anwesend",
        "guest": "Gast",
        "excused": "Fehlt entschuldigt",
        "unexcused": "Fehlt unentschuldigt",
    }
    return labels.get(status, status or "-")


def event_audit_snapshot(event):
    snapshot = {
        "Datum": event.event_date.isoformat() if event.event_date else "",
        "Status": event_status_label(event.status),
        "Bahnkosten": f"{cents_to_euro(event.lane_cost_cents)} €",
        "Notiz/Grund": event.note or "",
    }

    participants = EventParticipant.query.filter_by(event_id=event.id).all()
    penalty_types = PenaltyType.query.order_by(PenaltyType.sort_order, PenaltyType.name).all()

    for participant in sorted(participants, key=lambda item: participant_display_name(item).casefold()):
        name = participant_display_name(participant)
        snapshot[f"{name} · Status"] = event_status_label(participant.status)

        for penalty_type in penalty_types:
            penalty = ParticipantPenalty.query.filter_by(
                participant_id=participant.id,
                penalty_type_id=penalty_type.id,
            ).first()
            if not penalty:
                value = "0,00 €" if penalty_type.kind == "amount" else "0"
            elif penalty_type.kind == "amount":
                note = f" ({penalty.note})" if penalty.note else ""
                value = f"{cents_to_euro(penalty.amount_cents)} €{note}"
            else:
                value = str(penalty.quantity or 0)
            snapshot[f"{name} · {penalty_type.name}"] = value

    return snapshot



def cash_audit_snapshot(audit):
    return {
        "Prüfdatum": audit.audit_date.isoformat() if audit.audit_date else "",
        "Barkasse laut System": f"{cents_to_euro(audit.expected_cash_cents)} €",
        "Barkasse gezählt": f"{cents_to_euro(audit.counted_cash_cents)} €",
        "Barkasse Differenz": f"{cents_to_euro(audit.difference_cash_cents)} €",
        "Bank laut System": f"{cents_to_euro(audit.expected_bank_cents)} €",
        "Bank laut Auszug": f"{cents_to_euro(audit.statement_bank_cents)} €",
        "Bank Differenz": f"{cents_to_euro(audit.difference_bank_cents)} €",
        "Notiz": audit.note or "",
        "Bestätigt von": audit.confirmed_by_user.username if getattr(audit, "confirmed_by_user", None) else "",
        "Bestätigt am": audit.confirmed_at.isoformat() if getattr(audit, "confirmed_at", None) else "",
        "Prüfernotiz": getattr(audit, "auditor_note", None) or "",
    }


def annual_closing_snapshot(closing):
    return {
        "Jahr": closing.year,
        "Abschlussdatum": closing.closing_date.isoformat() if closing.closing_date else "",
        "Barkasse": f"{cents_to_euro(closing.cash_balance_cents)} €",
        "Bank": f"{cents_to_euro(closing.bank_balance_cents)} €",
        "Gesamtbestand": f"{cents_to_euro(closing.total_balance_cents)} €",
        "Offene Strafen": f"{cents_to_euro(closing.open_penalties_cents)} €",
        "Guthaben Mitglieder": f"{cents_to_euro(closing.member_credits_cents)} €",
        "Einnahmen im Jahr": f"{cents_to_euro(closing.income_cents)} €",
        "Ausgaben im Jahr": f"{cents_to_euro(closing.expense_cents)} €",
        "Kegelabende abgeschlossen": closing.event_count,
        "Kegelabende ausgefallen": closing.cancelled_event_count,
        "Kegelabende offen": closing.open_event_count,
        "Letzte Kassenprüfung": closing.last_cash_audit.audit_date.isoformat() if closing.last_cash_audit and closing.last_cash_audit.audit_date else "-",
        "Notiz": closing.note or "",
        "Bestätigt von": closing.confirmed_by_user.username if getattr(closing, "confirmed_by_user", None) else "",
        "Bestätigt am": closing.confirmed_at.isoformat() if getattr(closing, "confirmed_at", None) else "",
        "Prüfernotiz": getattr(closing, "auditor_note", None) or "",
    }

def account_balance(account):
    transactions = AccountTransaction.query.filter_by(account=account).all()
    return sum(transaction.amount_cents for transaction in transactions)


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



BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/app/backups"))
ACTIVE_DATABASE_PROFILE = get_active_database_profile()
DOCUMENT_DIR = get_document_dir(ACTIVE_DATABASE_PROFILE)
ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "gif", "webp", "doc", "docx", "xls", "xlsx", "txt", "csv", "json", "html", "htm", "rtf"}
DEFAULT_DOCUMENT_CATEGORIES = ["Bahnrechnungen", "Kassenprüfung", "Jahresabschluss", "Kegeltour", "Historische Importdaten", "Vereinsunterlagen", "Sonstiges"]
DATABASE_PATH = get_database_path(ACTIVE_DATABASE_PROFILE)


def document_categories():
    raw = setting_value("document_custom_categories", "[]")
    try:
        custom = json.loads(raw or "[]")
    except Exception:
        custom = []
    categories = []
    for category in DEFAULT_DOCUMENT_CATEGORIES + custom:
        category = (category or "").strip()
        if category and category not in categories:
            categories.append(category)
    return categories or ["Sonstiges"]


def save_custom_document_category(name):
    name = (name or "").strip()
    if not name:
        return None
    if len(name) > 80:
        name = name[:80].strip()
    categories = document_categories()
    if name in categories:
        return name
    custom = [c for c in categories if c not in DEFAULT_DOCUMENT_CATEGORIES]
    custom.append(name)
    set_setting_value("document_custom_categories", json.dumps(custom, ensure_ascii=False))
    return name


def backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def backup_file_list():
    folder = backup_dir()
    files = []
    for path in sorted(folder.glob("kegelkasse_backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        kind = "Automatisch" if "_auto_" in path.name else "Manuell"
        files.append({
            "name": path.name,
            "path": path,
            "size_bytes": stat.st_size,
            "size_label": human_file_size(stat.st_size),
            "created_at": datetime.fromtimestamp(stat.st_mtime),
            "kind": kind,
        })
    return files


def human_file_size(size_bytes):
    size = float(size_bytes or 0)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024


def safe_int_setting(key, default, minimum=None):
    try:
        value = int(setting_value(key, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def backup_settings():
    target_type = setting_value("backup_target_type", "webdav")
    if target_type == "local_path":
        target_type = "webdav"
    return {
        "enabled": setting_value("backup_enabled", "0") == "1",
        "interval": setting_value("backup_interval", "daily"),
        "time": setting_value("backup_time", "02:00"),
        "keep_days": safe_int_setting("backup_keep_days", 30, 1),
        "keep_count": safe_int_setting("backup_keep_count", 20, 1),
        "include_documents": setting_value("backup_include_documents", "1") == "1",
        "extra_target_enabled": setting_value("backup_extra_target_enabled", "0") == "1",
        "target_type": target_type,
        "extra_target_path": setting_value("backup_extra_target_path", ""),
        "webdav_url": setting_value("backup_webdav_url", ""),
        "webdav_username": setting_value("backup_webdav_username", ""),
        "webdav_password_set": bool(setting_value("backup_webdav_password", "")),
        "last_auto": setting_value("backup_last_auto", ""),
        "last_result": setting_value("backup_last_result", ""),
        "target_label": backup_target_label_raw(target_type),
    }


def backup_target_label_raw(target_type):
    labels = {
        "webdav": "WebDAV/Nextcloud",
        "dropbox": "Dropbox",
        "onedrive": "OneDrive",
        "google_drive": "Google Drive",
    }
    return labels.get(target_type or "webdav", "WebDAV/Nextcloud")


def backup_target_label(settings=None):
    settings = settings or backup_settings()
    if not settings.get("extra_target_enabled"):
        return "nur lokal"
    if settings.get("target_type") == "webdav":
        return "WebDAV/Nextcloud"
    return backup_target_label_raw(settings.get("target_type")) + " (vorbereitet)"

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


def sanitize_sqlite_settings_for_export(db_path):
    """Entfernt Geheimnisse aus der kopierten SQLite-Datei, bevor sie gezippt wird.

    Die laufende Produktivdatenbank bleibt unverändert. Nur die Export-/Backup-Kopie
    verliert SMTP-, WebDAV- und spätere Cloud-Token. Nach Restore/Import müssen diese
    Zugangsdaten neu eingetragen werden.
    """
    if not db_path or not Path(db_path).exists():
        return

    con = sqlite3.connect(str(db_path))
    try:
        table_names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in ("app_settings", "app_setting"):
            if table not in table_names:
                continue
            rows = con.execute(f'SELECT key FROM "{table}"').fetchall()
            for (key,) in rows:
                if is_secret_setting_key(key):
                    con.execute(f'UPDATE "{table}" SET value = ? WHERE key = ?', ("", key))
        con.commit()
    finally:
        con.close()


def database_info():
    table_count = 0
    row_count = 0
    db_size = 0
    if DATABASE_PATH.exists():
        db_size = DATABASE_PATH.stat().st_size
        try:
            conn = sqlite3.connect(str(DATABASE_PATH))
            cursor = conn.cursor()
            tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
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
        "db_size_label": human_file_size(db_size),
        "table_count": table_count,
        "row_count": row_count,
    }


def webdav_upload_file(source_path):
    base_url = (setting_value("backup_webdav_url", "") or "").strip()
    username = (setting_value("backup_webdav_username", "") or "").strip()
    password = setting_value("backup_webdav_password", "") or ""

    if not base_url:
        raise ValueError("WebDAV ist aktiviert, aber keine WebDAV-URL eingetragen.")
    if not base_url.lower().startswith(("http://", "https://")):
        raise ValueError("Die WebDAV-URL muss mit http:// oder https:// beginnen.")

    encoded_name = urllib.parse.quote(source_path.name)
    target_url = base_url.rstrip("/") + "/" + encoded_name

    data = source_path.read_bytes()
    request_obj = urllib.request.Request(target_url, data=data, method="PUT")
    request_obj.add_header("Content-Type", "application/zip")
    request_obj.add_header("Content-Length", str(len(data)))

    if username or password:
        token = (f"{username}:{password}").encode("utf-8")
        import base64
        request_obj.add_header("Authorization", "Basic " + base64.b64encode(token).decode("ascii"))

    try:
        with urllib.request.urlopen(request_obj, timeout=45) as response:
            if response.status not in (200, 201, 204):
                raise ValueError(f"WebDAV-Upload fehlgeschlagen: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"WebDAV-Upload fehlgeschlagen: HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"WebDAV nicht erreichbar: {exc.reason}") from exc

    return target_url


def copy_backup_to_extra_target(source_path):
    settings = backup_settings()
    if not settings.get("extra_target_enabled"):
        return None

    if settings.get("target_type") == "webdav":
        return webdav_upload_file(source_path)

    raise ValueError(f"Das Backup-Ziel '{backup_target_label_raw(settings.get('target_type'))}' ist vorbereitet, aber technisch noch nicht angebunden. Bitte aktuell WebDAV/Nextcloud verwenden.")





def club_import_dir():
    folder = backup_dir() / "club_imports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def validate_club_export_zip(path):
    """Prüft einen Vereins-Export und liefert eine Import-Vorschau."""
    result = {
        "ok": False,
        "errors": [],
        "warnings": [],
        "metadata": {},
        "counts": {},
        "documents_count": 0,
        "filename": path.name if path else "",
    }

    if not path or not path.exists():
        result["errors"].append("Datei nicht gefunden.")
        return result

    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            if "export_info.json" not in names:
                result["errors"].append("export_info.json fehlt. Das ist kein gültiger Vereins-Export.")
            else:
                try:
                    result["metadata"] = json.loads(zf.read("export_info.json").decode("utf-8"))
                except Exception:
                    result["errors"].append("export_info.json konnte nicht gelesen werden.")

            if "kegelkasse.db" not in names:
                result["errors"].append("kegelkasse.db fehlt.")

            export_type = result.get("metadata", {}).get("type")
            if export_type and export_type != "club_export":
                result["warnings"].append(f"Export-Typ ist '{export_type}', erwartet wurde 'club_export'.")

            result["documents_count"] = len([name for name in names if name.startswith("documents/") and not name.endswith("/")])

            if result["errors"]:
                return result

            temp_db = club_import_dir() / f".check_{path.stem}.sqlite.tmp"
            try:
                zf.extract("kegelkasse.db", club_import_dir())
                extracted = club_import_dir() / "kegelkasse.db"
                if temp_db.exists():
                    temp_db.unlink()
                extracted.rename(temp_db)

                con = sqlite3.connect(str(temp_db))
                try:
                    quick = con.execute("PRAGMA quick_check").fetchone()
                    if not quick or quick[0] != "ok":
                        result["errors"].append("SQLite quick_check ist fehlgeschlagen.")
                    table_names = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                    for table in ["user", "member", "bowling_event", "cashbook_entry", "document", "audit_log"]:
                        if table in table_names:
                            try:
                                result["counts"][table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                            except Exception:
                                result["counts"][table] = "?"
                    if "app_setting" in table_names:
                        try:
                            club = con.execute("SELECT value FROM app_setting WHERE key='club_name'").fetchone()
                            if club and club[0]:
                                result.setdefault("metadata", {})["club_name_from_db"] = club[0]
                        except Exception:
                            pass
                finally:
                    con.close()
            finally:
                try:
                    temp_db.unlink()
                except FileNotFoundError:
                    pass
    except zipfile.BadZipFile:
        result["errors"].append("ZIP-Datei ist ungültig oder beschädigt.")
    except Exception as exc:
        result["errors"].append(str(exc))

    result["ok"] = not result["errors"]
    return result


def import_club_export_zip(path):
    """Importiert einen geprüften Vereins-Export. Ersetzt aktuelle DB und Dokumente."""
    validation = validate_club_export_zip(path)
    if not validation.get("ok"):
        raise ValueError("Vereins-Export ist nicht gültig: " + "; ".join(validation.get("errors", [])))

    restore_before = create_database_backup(kind="manual")
    work_dir = club_import_dir() / f"import_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    work_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(work_dir)

    imported_db = work_dir / "kegelkasse.db"
    if not imported_db.exists():
        raise ValueError("Importierte Datenbank fehlt nach dem Entpacken.")

    # Bestehende Verbindungen lösen, damit die SQLite-Datei sicher ersetzt werden kann.
    db.session.remove()
    db.engine.dispose()

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(imported_db, DATABASE_PATH)

    imported_docs = work_dir / "documents"
    if imported_docs.exists():
        if DOCUMENT_DIR.exists():
            shutil.rmtree(DOCUMENT_DIR)
        shutil.copytree(imported_docs, DOCUMENT_DIR)

    # Neue DB initialisieren/migrieren und Import im neuen Revisionsprotokoll dokumentieren.
    db.engine.dispose()
    db.create_all()
    migrate_schema_extensions()
    audit_log(
        "Vereins-Import",
        "club_import_restored",
        "Vereins-Export importiert",
        details=f"Importiert aus: {path.name}; automatische Sicherung vorher: {restore_before.name}",
        object_type="ClubImport",
        old_value=restore_before.name,
        new_value=path.name,
    )
    db.session.commit()

    try:
        shutil.rmtree(work_dir)
    except Exception:
        pass

    return restore_before
def create_club_export(include_documents=True):
    """Erstellt einen kompletten Vereins-Export als ZIP.

    Unterschied zum normalen Backup:
    - Dateiname und Metadaten sind auf Umzug/Archivierung ausgelegt.
    - Format ist versioniert, damit spätere Importe in der öffentlichen Version Migrationen erkennen können.
    - Die Datei wird nicht als automatische Sicherung gezählt.
    """
    folder = backup_dir()
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    club_name = setting_value("club_name", "Kegelkasse") or "Kegelkasse"
    safe_club = secure_filename(club_name).strip("._-") or "kegelkasse"
    filename = f"kegelkasse_vereinsexport_{safe_club}_{stamp}.zip"
    target = folder / filename
    temp_db = folder / f".{filename}.sqlite.tmp"

    source = sqlite3.connect(str(DATABASE_PATH))
    dest = sqlite3.connect(str(temp_db))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    sanitize_sqlite_settings_for_export(temp_db)

    metadata = {
        "created_at": now.isoformat(timespec="seconds"),
        "app": "Kegelkasse",
        "type": "club_export",
        "format_version": 1,
        "database_file": DATABASE_PATH.name,
        "club_name": club_name,
        "include_documents": bool(include_documents),
        "secrets_exported": False,
        "secrets_hint": "SMTP-, WebDAV- und Cloud-Zugangsdaten werden aus Sicherheitsgründen nicht exportiert und müssen nach Import/Restore neu eingetragen werden.",
        "hint": "Vollständiger Vereins-Export für Umzug, Archivierung oder späteren Import in der öffentlichen Version.",
    }

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(temp_db, arcname="kegelkasse.db")
        zf.writestr("export_info.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        zf.writestr(
            "app_settings.json",
            json.dumps(sanitized_app_settings_dict(), ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "README_EXPORT.txt",
            "Kegelkasse Vereins-Export\n"
            "==========================\n\n"
            "Diese ZIP-Datei enthält den vollständigen Vereinsstand für Umzug, Archivierung oder späteren Import.\n\n"
            "Enthalten:\n"
            "- kegelkasse.db: SQLite-Datenbank inkl. Mitglieder, Buchungen, Kegelabende, Revisionen und Einstellungen\n"
            "- app_settings.json: Einstellungen zusätzlich lesbar als JSON, ohne geheime Zugangsdaten\n"
            "- export_info.json: Export-Metadaten und Format-Version\n"
            "- documents/: Dokumentenablage, sofern beim Export ausgewählt\n\n"
            "Sicherheitshinweis: SMTP-, WebDAV- und Cloud-Passwörter/Tokens werden nicht exportiert.\n"
            "Diese Zugangsdaten müssen nach Import oder Wiederherstellung neu eingetragen werden.\n\n"
            "Hinweis: Dieser Export ersetzt kein regelmäßiges Backup mit zweitem Speicherziel.\n",
        )
        if include_documents and DOCUMENT_DIR.exists():
            for doc_path in DOCUMENT_DIR.rglob("*"):
                if doc_path.is_file():
                    zf.write(doc_path, arcname=f"documents/{doc_path.relative_to(DOCUMENT_DIR)}")

    try:
        temp_db.unlink()
    except FileNotFoundError:
        pass

    return target
    
def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
def create_database_backup(kind="manual"):
    folder = backup_dir()
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    filename = f"kegelkasse_backup_{'auto_' if kind == 'auto' else ''}{stamp}.zip"
    target = folder / filename
    temp_db = folder / f".{filename}.sqlite.tmp"

    # SQLite-Backup API nutzt einen konsistenten Snapshot, statt die laufende Datei roh zu kopieren.
    source = sqlite3.connect(str(DATABASE_PATH))
    dest = sqlite3.connect(str(temp_db))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    sanitize_sqlite_settings_for_export(temp_db)

    include_documents = backup_settings().get("include_documents", True)

    metadata = {
        "format_version": 3,
        "backup_uuid": str(uuid.uuid4()),
        "created_at": now.isoformat(timespec="seconds"),
        "created_by": current_user.username if current_user and current_user.is_authenticated else "system",
        "kind": kind,
        "app": "Kegelkasse",
        "app_version": APP_VERSION,
        "database_profile": get_active_database_profile(),
        "club_name": setting_value("club_name", "Alle 8te") or "Alle 8te",
        "database_file": DATABASE_PATH.name,
        "include_documents": include_documents,
        "includes": {
            "database": True,
            "documents": include_documents,
            "settings": True,
            "secrets": False,
        },
        "statistics": {
            "members": Member.query.count(),
            "events": BowlingEvent.query.count(),
            "cashbook_entries": CashbookEntry.query.count(),
            "documents": Document.query.filter(Document.deleted_at.is_(None)).count(),
        },
        "secrets_exported": False,
        "secrets_hint": "SMTP-, WebDAV- und Cloud-Zugangsdaten werden aus Sicherheitsgründen nicht in Backup-ZIPs gespeichert und müssen nach Restore neu eingetragen werden.",
    }

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(temp_db, arcname="kegelkasse.db")
        zf.writestr("backup_info.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        zf.writestr(
            "README_BACKUP.txt",
            "Kegelkasse Backup\n"
            "=================\n\n"
            f"Erstellt: {metadata['created_at']}\n"
            f"Version: {metadata['app_version']}\n"
            f"Verein: {metadata['club_name']}\n"
            f"Backup-ID: {metadata['backup_uuid']}\n\n"
            "Dieses Backup enthält die Kegelkasse-Datenbank"
            + (" und die Dokumentenablage" if metadata["include_documents"] else "")
            + ".\n\n"
            "Vor einer Wiederherstellung erstellt die Kegelkasse automatisch eine Sicherheitskopie der aktuellen Daten.\n\n"
            "Sicherheitshinweis: SMTP-, WebDAV- und Cloud-Zugangsdaten werden nicht exportiert und müssen nach einer Wiederherstellung neu eingetragen werden.\n",
        )
        # Einstellungen zusätzlich lesbar ablegen. Die eigentlichen Daten sind weiterhin in der DB.
        zf.writestr(
            "app_settings.json",
            json.dumps(sanitized_app_settings_dict(), ensure_ascii=False, indent=2),
        )
        if metadata["include_documents"] and DOCUMENT_DIR.exists():
            for doc_path in DOCUMENT_DIR.rglob("*"):
                if doc_path.is_file():
                    zf.write(doc_path, arcname=f"documents/{doc_path.relative_to(DOCUMENT_DIR)}")

    try:
        temp_db.unlink()
    except FileNotFoundError:
        pass

    copied_to = None
    try:
        copied_to = copy_backup_to_extra_target(target)
        set_setting_value("backup_last_result", f"OK: {target.name}" + (f"; Kopie: {copied_to}" if copied_to else ""))
        db.session.commit()
    except Exception as exc:
        set_setting_value("backup_last_result", f"Lokales Backup OK, Kopie fehlgeschlagen: {exc}")
        db.session.commit()
        # Lokale Sicherung bleibt gültig; den Fehler zeigen wir dem Benutzer zusätzlich an.
        if kind == "manual":
            raise

    return target

def cleanup_old_backups():
    settings = backup_settings()
    keep_days = max(1, settings["keep_days"])
    keep_count = max(1, settings.get("keep_count", 20))
    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = []

    auto_files = [item for item in backup_file_list() if item["kind"] == "Automatisch"]
    for index, item in enumerate(auto_files):
        too_old = item["created_at"] < cutoff
        too_many = index >= keep_count
        if too_old or too_many:
            try:
                item["path"].unlink()
                deleted.append(item["name"])
            except FileNotFoundError:
                pass
    return deleted

def restore_database_from_backup(path):
    if not path.exists() or path.parent != backup_dir():
        raise ValueError("Sicherungsdatei wurde nicht gefunden.")

    restore_before = create_database_backup(kind="manual")

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    work_dir = backup_dir() / f".restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    temp_restore = DATABASE_PATH.parent / f".restore_{DATABASE_PATH.name}.tmp"

    try:
        if temp_restore.exists():
            temp_restore.unlink()
        work_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            if "kegelkasse.db" not in names:
                raise ValueError("Die Sicherung enthält keine kegelkasse.db.")
            zf.extract("kegelkasse.db", work_dir)
            for name in names:
                if name.startswith("documents/") and not name.endswith("/"):
                    zf.extract(name, work_dir)

        extracted_db = work_dir / "kegelkasse.db"
        if not extracted_db.exists():
            raise ValueError("Die Datenbank konnte aus der Sicherung nicht entpackt werden.")

        # Bestehende SQLAlchemy-Verbindungen lösen, bevor die SQLite-Datei ersetzt wird.
        db.session.remove()
        db.engine.dispose()

        # Nicht per rename/move aus dem Backup-Ordner ersetzen:
        # Backup- und Datenbankverzeichnis können in Docker verschiedene Mounts sein.
        # copy2 funktioniert zuverlässig über Dateisystemgrenzen hinweg; das finale replace
        # passiert anschließend im Datenbankverzeichnis selbst.
        shutil.copy2(extracted_db, temp_restore)
        os.replace(temp_restore, DATABASE_PATH)

        extracted_documents = work_dir / "documents"
        if extracted_documents.exists():
            if DOCUMENT_DIR.exists():
                shutil.rmtree(DOCUMENT_DIR)
            DOCUMENT_DIR.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(extracted_documents, DOCUMENT_DIR)

        db.engine.dispose()
        db.create_all()
        migrate_schema_extensions()

    finally:
        for leftover in (temp_restore,):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass
        try:
            shutil.rmtree(work_dir)
        except Exception:
            pass

    return restore_before


def verify_backup_file(path):
    if not path.exists() or path.parent != backup_dir():
        raise ValueError("Sicherungsdatei wurde nicht gefunden.")

    result = {
        "database": False,
        "documents": 0,
        "settings": False,
        "metadata": False,
        "quick_check": "nicht geprüft",
    }

    temp_db = backup_dir() / f".verify_{path.stem}.sqlite.tmp"
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            if "kegelkasse.db" not in names:
                raise ValueError("Die Sicherung enthält keine kegelkasse.db.")
            result["metadata"] = "backup_info.json" in names
            result["settings"] = "app_settings.json" in names
            result["documents"] = len([name for name in names if name.startswith("documents/") and not name.endswith("/")])
            zf.extract("kegelkasse.db", backup_dir())

        extracted = backup_dir() / "kegelkasse.db"
        extracted.replace(temp_db)

        conn = sqlite3.connect(str(temp_db))
        try:
            check = conn.execute("PRAGMA quick_check").fetchone()
            result["quick_check"] = check[0] if check else "kein Ergebnis"
            result["database"] = result["quick_check"].lower() == "ok"
        finally:
            conn.close()

        if not result["database"]:
            raise ValueError(f"SQLite-Prüfung fehlgeschlagen: {result['quick_check']}")

        parts = ["Datenbank lesbar"]
        if result["metadata"]:
            parts.append("Metadaten vorhanden")
        if result["settings"]:
            parts.append("Einstellungen vorhanden")
        parts.append(f"{result['documents']} Dokument(e)")
        return "; ".join(parts)
    finally:
        try:
            temp_db.unlink()
        except FileNotFoundError:
            pass



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


def clean_username(username):
    return (username or "").strip()


def username_is_available(username, current_user_id=None):
    query = User.query.filter(db.func.lower(User.username) == username.lower())
    if current_user_id:
        query = query.filter(User.id != current_user_id)
    return query.first() is None


def audit_log(category, action, title, details=None, object_type=None, object_id=None, old_value=None, new_value=None):
    """Schreibt einen revisionsrelevanten Änderungsvermerk.

    Bewusst nicht für jeden Plus/Minus-Klick bei Strafen, sondern nur für
    abgeschlossene Aktionen wie Buchungen, Storno, Einstellungen, Mitglieder,
    Strafarten und Kegelabend-Statuswechsel.
    """
    try:
        user_id = current_user.id if current_user and current_user.is_authenticated else None
        username = current_user.username if current_user and current_user.is_authenticated else None
    except Exception:
        user_id = None
        username = None

    db.session.add(AuditLog(
        user_id=user_id,
        username=username,
        category=category,
        action=action,
        object_type=object_type,
        object_id=object_id,
        title=title,
        details=details,
        old_value=old_value,
        new_value=new_value,
    ))


def first_weekday_of_month(year, month, weekday):
    day = datetime(year, month, 1).date()
    offset = (weekday - day.weekday()) % 7
    return day + timedelta(days=offset)


def add_month(year, month):
    if month == 12:
        return year + 1, 1
    return year, month + 1


def last_day_of_month(year, month):
    next_year, next_month = add_month(year, month)
    return (datetime(next_year, next_month, 1).date() - timedelta(days=1)).day


def nth_weekday_of_month(year, month, weekday, nth):
    first = first_weekday_of_month(year, month, weekday)
    return first + timedelta(days=7 * (nth - 1))


def last_weekday_of_month(year, month, weekday):
    day = datetime(year, month, last_day_of_month(year, month)).date()
    offset = (day.weekday() - weekday) % 7
    return day - timedelta(days=offset)


def next_event_date_from_rhythm(reference_date=None):
    today = reference_date or datetime.today().date()
    rhythm_type = setting_value("event_rhythm_type", "weeks")

    latest_event = BowlingEvent.query.order_by(BowlingEvent.event_date.desc()).first()
    anchor = latest_event.event_date if latest_event else today

    if rhythm_type in ("monthly_first_weekday", "monthly_nth_weekday", "monthly_last_weekday"):
        try:
            weekday = int(setting_value("event_rhythm_weekday", "4"))
        except ValueError:
            weekday = 4
        weekday = min(max(weekday, 0), 6)

        try:
            nth = int(setting_value("event_rhythm_nth", "1"))
        except ValueError:
            nth = 1
        nth = min(max(nth, 1), 4)

        year, month = today.year, today.month
        while True:
            if rhythm_type == "monthly_last_weekday":
                candidate = last_weekday_of_month(year, month, weekday)
            else:
                candidate = nth_weekday_of_month(year, month, weekday, nth)
                if candidate.month != month:
                    candidate = last_weekday_of_month(year, month, weekday)
            if candidate >= today:
                return candidate
            year, month = add_month(year, month)

    if rhythm_type == "monthly_day":
        try:
            day = int(setting_value("event_rhythm_month_day", "1"))
        except ValueError:
            day = 1
        day = min(max(day, 1), 31)
        year, month = today.year, today.month
        while True:
            candidate_day = min(day, last_day_of_month(year, month))
            candidate = datetime(year, month, candidate_day).date()
            if candidate >= today:
                return candidate
            year, month = add_month(year, month)

    try:
        weeks = int(setting_value("event_rhythm_weeks", "4"))
    except ValueError:
        weeks = 4
    weeks = max(1, weeks)
    try:
        weekday = int(setting_value("event_rhythm_weekday", "4"))
    except ValueError:
        weekday = 4
    weekday = min(max(weekday, 0), 6)

    # Bei Wochen-Rhythmus zusätzlich den gewünschten Wochentag beachten.
    # Ohne vorhandenen Abend nehmen wir den nächsten passenden Wochentag ab heute.
    if not latest_event:
        offset = (weekday - today.weekday()) % 7
        return today + timedelta(days=offset)

    step = timedelta(weeks=weeks)
    candidate = anchor + step
    candidate = candidate + timedelta(days=(weekday - candidate.weekday()) % 7)
    while candidate < today:
        candidate = candidate + step
    return candidate

def next_event_label(next_date):
    today = datetime.today().date()
    if next_date == today:
        return "heute"
    delta = (next_date - today).days
    if delta > 0:
        return f"in {delta} Tagen"
    return f"vor {abs(delta)} Tagen"


def average_rounding_label(raw_cents, rounded_cents):
    if raw_cents == rounded_cents:
        return "exakt auf 0,10 €"
    if rounded_cents > raw_cents:
        return "aufgerundet auf 0,10 €"
    return "abgerundet auf 0,10 €"


def current_rate_cents(key, target_date):
    rate = (
        RateSetting.query
        .filter(RateSetting.key == key)
        .filter(RateSetting.valid_from <= target_date)
        .order_by(RateSetting.valid_from.desc())
        .first()
    )

    return rate.amount_cents if rate else 0


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

    db.session.commit()


def migrate_viewer_roles_to_member():
    """Alte Rollenbezeichnung viewer in member überführen."""
    changed = False
    for user in User.query.filter_by(role="viewer").all():
        user.role = "member"
        changed = True
    if changed:
        db.session.commit()


def ensure_column(table_name, column_name, column_sql):
    """Robuste SQLite-Migration für bestehende Installationen.

    Gibt True zurück, wenn eine Spalte neu angelegt wurde. Dadurch können
    Migrationen beim Containerstart nachvollziehbar in Docker-Logs und im
    Revisionsprotokoll dokumentiert werden.
    """
    allowed_ident = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
    if not table_name or not column_name:
        return False
    if any(ch not in allowed_ident for ch in table_name + column_name):
        raise ValueError(f"Ungültiger Tabellen-/Spaltenname: {table_name}.{column_name}")

    rows = db.session.execute(db.text(f"PRAGMA table_info({table_name})")).fetchall()
    if not rows:
        print(f"[DB-Migration] Tabelle nicht gefunden oder noch leer: {table_name}")
        return False

    existing = {row[1] for row in rows}
    if column_name in existing:
        return False

    db.session.execute(db.text(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}"))
    db.session.commit()
    print(f"[DB-Migration] Spalte angelegt: {table_name}.{column_name}")
    return True


def record_schema_migration(changes):
    """Dokumentiert automatische Schema-Migrationen ohne angemeldeten Benutzer."""
    if not changes:
        return

    try:
        details = "\n".join(changes)
        db.session.add(AuditLog(
            user_id=None,
            username="System",
            category="system",
            action="schema_migration",
            object_type="database",
            title="Automatische Datenbankmigration ausgeführt",
            details=details,
            new_value=details,
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB-Migration] Revisionsprotokoll konnte nicht geschrieben werden: {exc}")


def migrate_schema_extensions():
    """Ergänzt fehlende Spalten in älteren SQLite-Datenbanken.

    Wichtig für ZIP-Updates: neuer Code darf nicht mit Internal Server Error
    starten, nur weil eine bestehende Vereinsdatenbank einzelne neue Spalten
    noch nicht besitzt.
    """
    changes = []

    def add(table, column, sql):
        if ensure_column(table, column, sql):
            changes.append(f"{table}.{column}")

    add("users", "email", "email VARCHAR(255)")
    add("users", "is_system_user", "is_system_user BOOLEAN NOT NULL DEFAULT 0")

    add("app_settings", "created_at", "created_at DATETIME")
    add("app_settings", "updated_at", "updated_at DATETIME")

    add("penalty_types", "target_mode", "target_mode VARCHAR(20) NOT NULL DEFAULT 'self'")
    add("monthly_contribution_payments", "paid_date", "paid_date DATE")
    add("members", "monthly_value_day", "monthly_value_day INTEGER")

    add("interest_bookings", "tax_rate_basis_points", "tax_rate_basis_points INTEGER DEFAULT 0")
    add("interest_bookings", "tax_cents", "tax_cents INTEGER DEFAULT 0")
    add("interest_bookings", "net_interest_cents", "net_interest_cents INTEGER DEFAULT 0")
    add("interest_bookings", "capital_gains_tax_cents", "capital_gains_tax_cents INTEGER DEFAULT 0")
    add("interest_bookings", "solidarity_tax_cents", "solidarity_tax_cents INTEGER DEFAULT 0")
    add("interest_bookings", "church_tax_cents", "church_tax_cents INTEGER DEFAULT 0")

    # Dokumente 2.0: Archivieren statt physisch löschen.
    add("documents", "deleted_at", "deleted_at DATETIME")
    add("documents", "deleted_by_user_id", "deleted_by_user_id INTEGER")

    # Kassenprüfer-/Bestätigungsfelder für bestehende SQLite-Datenbanken.
    for table_name in ["cash_audits", "annual_closings", "interest_settings", "interest_bookings"]:
        add(table_name, "confirmed_by_user_id", "confirmed_by_user_id INTEGER")
        add(table_name, "confirmed_at", "confirmed_at DATETIME")
        add(table_name, "auditor_note", "auditor_note TEXT")

    db.session.execute(db.text("UPDATE interest_bookings SET net_interest_cents = actual_interest_cents WHERE net_interest_cents IS NULL OR net_interest_cents = 0"))
    db.session.execute(db.text("UPDATE interest_bookings SET capital_gains_tax_cents = tax_cents WHERE (capital_gains_tax_cents IS NULL OR capital_gains_tax_cents = 0) AND tax_cents > 0"))
    db.session.commit()

    if changes:
        print("[DB-Migration] Angelegte Spalten: " + ", ".join(changes))
        record_schema_migration(changes)
    else:
        print("[DB-Migration] Schema aktuell, keine Änderungen nötig.")


def event_has_started_entries(event):
    """Prüft, ob für den Abend schon echte Erfassungsdaten vorhanden sind.

    Gäste allein zählen noch nicht, damit mehrere Gäste nacheinander angelegt werden können.
    """
    participants = EventParticipant.query.filter_by(event_id=event.id).all()

    for participant in participants:
        for penalty in participant.dynamic_penalties:
            if (penalty.quantity or 0) > 0:
                return True
            if (penalty.amount_cents or 0) > 0:
                return True
            if penalty.note:
                return True

    return False


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


def update_member_login(member):
    create_login = request.form.get("create_login") == "on"
    username = request.form.get("username", "").strip()
    role = request.form.get("role", "member").strip()
    password = request.form.get("password", "")

    if not create_login:
        if member.user:
            member.user.active = False
        return

    if not username:
        flash("Für ein Login-Konto muss ein Benutzername angegeben werden.", "danger")
        return

    if member.user:
        user = member.user
        user.username = username
        user.role = role
        user.active = True

        if password:
            user.password_hash = generate_password_hash(password)
    else:
        if not password:
            flash("Für ein neues Login-Konto muss ein Passwort gesetzt werden.", "danger")
            return

        existing = User.query.filter_by(username=username).first()
        if existing:
            flash("Dieser Benutzername ist bereits vergeben.", "danger")
            return

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            role=role,
            active=True,
        )

        db.session.add(user)
        db.session.flush()
        member.user_id = user.id


def validate_email_format(value):
    value = (value or "").strip()
    if not value:
        return True
    return re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", value) is not None


def iban_is_valid(iban):
    """Prüft IBAN-Format und Prüfziffer. Leer ist erlaubt."""
    iban = re.sub(r"\s+", "", (iban or "")).upper()
    if not iban:
        return True
    if not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = ""
    for ch in rearranged:
        if ch.isdigit():
            numeric += ch
        elif "A" <= ch <= "Z":
            numeric += str(ord(ch) - 55)
        else:
            return False
    remainder = 0
    for ch in numeric:
        remainder = (remainder * 10 + int(ch)) % 97
    return remainder == 1


def bic_is_valid(bic):
    bic = re.sub(r"\s+", "", (bic or "")).upper()
    if not bic:
        return True
    return re.match(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$", bic) is not None


def paypal_link_is_valid(value):
    value = (value or "").strip()
    if not value:
        return True
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    return parsed.scheme in ("http", "https") and (host.endswith("paypal.me") or host.endswith("paypal.com"))


@app.route("/setup", methods=["GET", "POST"])
def first_start_setup():
    """Startbildschirm für neue/leere Installationen.

    Neue Nutzer wählen zuerst den passenden Einstieg: neu anlegen, Backup
    wiederherstellen, Daten importieren oder Demo starten. Die konkreten
    Formulare erscheinen erst nach der Auswahl.
    """
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


@app.route("/")
@login_required
def dashboard():
    today = datetime.today().date()
    member_count = Member.query.count()
    active_members = Member.query.filter_by(active=True).order_by(Member.first_name.asc(), Member.last_name.asc()).all()
    active_member_count = len(active_members)
    next_date = next_event_date_from_rhythm()

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

    active_event = (
        BowlingEvent.query
        .filter(BowlingEvent.status.in_(("open", "settlement", "lane_cost")))
        .order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc())
        .first()
    )
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

        pump_rank = next((index + 1 for index, row in enumerate(pump_rows) if row["member_id"] == current_member.id), None)
        absence_rank = next((index + 1 for index, row in enumerate(absence_rows) if row.get("member_id") == current_member.id), None)

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

    if show_admin_tasks and open_penalties_cents > 0:
        dashboard_tasks.append({
            "priority": "medium",
            "icon": "💶",
            "title": "Offene Strafkonten prüfen",
            "description": f"{len(open_member_rows)} Mitglieder · {cents_to_euro(open_penalties_cents)} € offen",
            "url": url_for("penalty_balances"),
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

@app.route("/test-database", methods=["GET"])
def test_database_page():
    if not app.config.get("DEVELOPER_MODE", False):
        abort(404)
    """Versteckte Testdatenbank-Auswahl für die Testphase.

    Die Loginseite bleibt für den normalen Vereinsbetrieb aufgeräumt. Wer testen
    möchte, erreicht die Auswahl bewusst über das Logo bzw. direkt über diese URL.
    """
    return render_template("test_database.html")


@app.route("/test-database/switch", methods=["POST"])
def switch_database_profile():
    if not app.config.get("DEVELOPER_MODE", False):
        abort(404)
    """Temporärer Testmodus: aktive Datenbank vor dem Login umschalten.

    Der Wechsel betrifft nur die verwendete SQLite-Datei und den Dokumentenordner.
    Die echte Vereinsdatenbank bleibt unter /app/database/kegelkasse.db unverändert.
    Damit SQLAlchemy sauber auf die andere Datei verbindet, wird der Containerprozess
    kurz beendet. Docker startet ihn wegen restart: unless-stopped automatisch neu.
    """
    profile = normalize_database_profile(request.form.get("database_profile"))
    reset_test_database = request.form.get("reset_test_database") == "1"

    if profile == "production" and reset_test_database:
        flash("Die echte Vereinsdatenbank kann über den Testmodus nicht zurückgesetzt werden.", "danger")
        return redirect(url_for("login"))

    if reset_test_database and TEST_DATABASE_PROFILES[profile].get("is_test"):
        test_db_path = get_database_path(profile)
        test_doc_dir = get_document_dir(profile)
        try:
            if test_db_path.exists():
                test_db_path.unlink()
            if test_doc_dir.exists():
                shutil.rmtree(test_doc_dir)
        except OSError as exc:
            flash(f"Testdatenbank konnte nicht zurückgesetzt werden: {exc}", "danger")
            return redirect(url_for("login"))

    set_active_database_profile(profile)

    restart_kegelkasse_process()
    return """
    <!doctype html>
    <html lang=\"de\">
    <head>
        <meta charset=\"utf-8\">
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
        <meta http-equiv=\"refresh\" content=\"5;url=/login\">
        <title>Datenbank wird gewechselt</title>
        <style>
            body { font-family: Arial, sans-serif; background:#f5f7fb; color:#18212f; padding:30px; }
            .box { max-width:620px; margin:60px auto; background:white; border:1px solid #e5e7eb; border-radius:16px; padding:24px; box-shadow:0 10px 30px rgba(15,23,42,.07); }
        </style>
    </head>
    <body><div class=\"box\">
        <h1>Datenbank wird gewechselt …</h1>
        <p>Die Kegelkasse startet kurz neu. Danach wird die Loginseite automatisch neu geladen.</p>
        <p>Falls nichts passiert: <a href=\"/login\">Loginseite neu öffnen</a>.</p>
    </div></body></html>
    """


@app.route("/test-database/control", methods=["POST"])
def test_database_control():
    if not app.config.get("DEVELOPER_MODE", False):
        abort(404)
    """Schnellaktionen im Testmodus-Banner.

    Speichern ist bei SQLite automatisch: Beim Zurückwechseln zur echten Datenbank
    wird die aktive Testdatenbank nicht gelöscht. Reset/Löschen sind nur für Testprofile erlaubt.
    """
    active_profile = get_active_database_profile()
    action = (request.form.get("action") or "").strip()

    if action == "switch_production":
        set_active_database_profile("production")
        flash("Aktueller Teststand bleibt gespeichert. Es wird zur echten Vereinsdatenbank gewechselt.", "success")
        restart_kegelkasse_process()
        return """
        <!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><meta http-equiv=\"refresh\" content=\"5;url=/login\"><title>Datenbank wird gewechselt</title><style>body{font-family:Arial,sans-serif;background:#f5f7fb;color:#18212f;padding:30px}.box{max-width:620px;margin:60px auto;background:white;border:1px solid #e5e7eb;border-radius:16px;padding:24px;box-shadow:0 10px 30px rgba(15,23,42,.07)}</style></head><body><div class=\"box\"><h1>Zur echten Vereinsdatenbank wechseln …</h1><p>Der aktuelle Teststand bleibt in seiner Testdatenbank gespeichert.</p><p>Falls nichts passiert: <a href=\"/login\">Loginseite neu öffnen</a>.</p></div></body></html>
        """

    if not TEST_DATABASE_PROFILES.get(active_profile, {}).get("is_test"):
        flash("Diese Aktion ist nur im Demo-/Testmodus möglich. Die echte Vereinsdatenbank bleibt geschützt.", "danger")
        return redirect(url_for("login"))

    if action == "reset_current_test":
        try:
            reset_or_delete_test_database(active_profile, mode="reset")
            flash("Testdatenbank wurde auf den vorgesehenen Ausgangsstand zurückgesetzt.", "success")
        except Exception as exc:
            flash(f"Testdatenbank konnte nicht zurückgesetzt werden: {exc}", "danger")
            return redirect(url_for("login"))
        restart_kegelkasse_process()
        return """
        <!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><meta http-equiv=\"refresh\" content=\"5;url=/login\"><title>Testdatenbank wird zurückgesetzt</title><style>body{font-family:Arial,sans-serif;background:#f5f7fb;color:#18212f;padding:30px}.box{max-width:620px;margin:60px auto;background:white;border:1px solid #e5e7eb;border-radius:16px;padding:24px;box-shadow:0 10px 30px rgba(15,23,42,.07)}</style></head><body><div class=\"box\"><h1>Testdatenbank wird zurückgesetzt …</h1><p>Die echte Vereinsdatenbank bleibt unverändert.</p><p>Falls nichts passiert: <a href=\"/login\">Loginseite neu öffnen</a>.</p></div></body></html>
        """

    if action == "delete_current_test":
        confirm = (request.form.get("confirm_delete") or "").strip().upper()
        if confirm != "LÖSCHEN" and confirm != "LOESCHEN":
            flash("Zum vollständigen Löschen der Testdatenbank bitte LÖSCHEN eintragen.", "danger")
            return redirect(url_for("login"))
        try:
            reset_or_delete_test_database(active_profile, mode="delete")
            flash("Testdatenbank wurde vollständig gelöscht. Beim nächsten Start beginnt sie leer.", "success")
        except Exception as exc:
            flash(f"Testdatenbank konnte nicht gelöscht werden: {exc}", "danger")
            return redirect(url_for("login"))
        restart_kegelkasse_process()
        return """
        <!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><meta http-equiv=\"refresh\" content=\"5;url=/setup\"><title>Testdatenbank wird gelöscht</title><style>body{font-family:Arial,sans-serif;background:#f5f7fb;color:#18212f;padding:30px}.box{max-width:620px;margin:60px auto;background:white;border:1px solid #e5e7eb;border-radius:16px;padding:24px;box-shadow:0 10px 30px rgba(15,23,42,.07)}</style></head><body><div class=\"box\"><h1>Testdatenbank wird gelöscht …</h1><p>Danach startet diese Testdatenbank leer mit dem Einrichtungsassistenten.</p><p>Falls nichts passiert: <a href=\"/setup\">Setup öffnen</a>.</p></div></body></html>
        """

    flash("Unbekannte Testdatenbank-Aktion.", "danger")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username, active=True).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for("dashboard"))

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


@app.route("/admin", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_area():
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
    return render_template(
        "admin.html",
        users=users,
        password_policy=password_policy(),
        password_policy_text=password_policy_text(),
        reset_tokens=active_password_reset_tokens(),
        mail_settings=mail_settings(),
        mail_settings_summary=mail_settings_summary(),
    )



def document_allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_DOCUMENT_EXTENSIONS


def document_date_from_form(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def document_summary(doc):
    links = []
    if doc.event:
        links.append(f"Kegelabend {doc.event.event_date.strftime('%d.%m.%Y')}")
    if doc.cashbook_entry:
        links.append(f"Kassenbuch #{doc.cashbook_entry.id}: {doc.cashbook_entry.reason}")
    return (
        f"Titel: {audit_value(doc.title)}\n"
        f"Kategorie: {audit_value(doc.category)}\n"
        f"Datum: {doc.document_date.strftime('%d.%m.%Y') if doc.document_date else '-'}\n"
        f"Datei: {audit_value(doc.original_filename)}\n"
        f"Größe: {doc.size_label()}\n"
        f"Zuordnung: {audit_value('; '.join(links) if links else '-') }"
    )


def document_extension(doc):
    filename = (doc.original_filename or doc.stored_filename or "").lower()
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1]


def document_preview_type(doc):
    ext = document_extension(doc)
    if ext in {"jpg", "jpeg", "png", "gif", "webp"}:
        return "image"
    if ext == "pdf":
        return "pdf"
    if ext in {"txt", "csv", "json", "html", "htm", "rtf"}:
        return "text"
    return "download_only"


@app.route("/documents", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def documents_page():
    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)

    if request.method == "POST":
        form_action = request.form.get("form_action", "upload")

        if form_action == "upload":
            title = (request.form.get("title") or "").strip()
            category = request.form.get("category") or "Sonstiges"
            new_category = (request.form.get("new_category") or "").strip()
            if category == "__new__":
                category = save_custom_document_category(new_category) or "Sonstiges"
            description = (request.form.get("description") or "").strip()
            document_date = document_date_from_form(request.form.get("document_date"))
            event_id = request.form.get("event_id") or None
            cashbook_entry_id = request.form.get("cashbook_entry_id") or None
            file = request.files.get("document_file")

            if not title:
                flash("Bitte einen Titel angeben.", "error")
                return redirect(url_for("documents_page"))
            if not file or not file.filename:
                flash("Bitte eine Datei auswählen.", "error")
                return redirect(url_for("documents_page"))
            if not document_allowed(file.filename):
                flash("Dieser Dateityp ist nicht erlaubt. Erlaubt sind PDF, Bilder, Text, CSV, JSON, HTML, RTF, DOCX und XLSX.", "error")
                return redirect(url_for("documents_page"))

            original_filename = secure_filename(file.filename)
            suffix = original_filename.rsplit(".", 1)[1].lower() if "." in original_filename else "bin"
            stored_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(8)}.{suffix}"
            target = DOCUMENT_DIR / stored_filename
            file.save(target)
            size = target.stat().st_size if target.exists() else 0

            document = Document(
                title=title,
                category=category if category in document_categories() else "Sonstiges",
                document_date=document_date,
                description=description or None,
                original_filename=original_filename,
                stored_filename=stored_filename,
                mime_type=file.mimetype,
                file_size=size,
                event_id=int(event_id) if event_id else None,
                cashbook_entry_id=int(cashbook_entry_id) if cashbook_entry_id else None,
                uploaded_by_user_id=current_user.id,
            )
            db.session.add(document)
            db.session.commit()

            audit_log(
                "Dokumente",
                "document_uploaded",
                f"Dokument hochgeladen: {document.title}",
                details=document_summary(document),
                object_type="document",
                object_id=document.id,
                new_value=document_summary(document),
            )
            flash("Dokument wurde hochgeladen.", "success")
            return redirect(url_for("documents_page"))

        if form_action in ["delete", "archive"] and current_user.role in ["admin", "cashier"]:
            doc = Document.query.get_or_404(int(request.form.get("document_id") or 0))
            old_summary = document_summary(doc)
            if not doc.deleted_at:
                doc.deleted_at = datetime.utcnow()
                doc.deleted_by_user_id = current_user.id
                db.session.commit()
                audit_log(
                    "Dokumente",
                    "document_archived",
                    f"Dokument archiviert: {doc.title}",
                    details=old_summary,
                    object_type="document",
                    object_id=doc.id,
                    old_value=old_summary,
                    new_value=document_summary(doc),
                )
                if doc.is_protected_category():
                    flash("Revisionsrelevantes Dokument wurde archiviert. Es bleibt als Nachweis erhalten und ist über 'Archivierte anzeigen' weiterhin einsehbar.", "success")
                else:
                    flash("Dokument wurde archiviert. Es bleibt auf dem Server erhalten und kann bei Bedarf wieder eingeblendet werden.", "success")
            return redirect(url_for("documents_page"))

        if form_action == "restore" and current_user.role in ["admin", "cashier"]:
            doc = Document.query.get_or_404(int(request.form.get("document_id") or 0))
            old_summary = document_summary(doc)
            doc.deleted_at = None
            doc.deleted_by_user_id = None
            db.session.commit()
            audit_log(
                "Dokumente",
                "document_restored",
                f"Dokument wiederhergestellt: {doc.title}",
                details=document_summary(doc),
                object_type="document",
                object_id=doc.id,
                old_value=old_summary,
                new_value=document_summary(doc),
            )
            flash("Dokument wurde wieder eingeblendet.", "success")
            return redirect(url_for("documents_page"))

    category_filter = request.args.get("category", "all")
    search = (request.args.get("q") or "").strip()
    show_archived = request.args.get("show_archived") == "1"
    query = Document.query
    if not show_archived:
        query = query.filter(Document.deleted_at.is_(None))
    if category_filter != "all":
        query = query.filter(Document.category == category_filter)
    if search:
        like = f"%{search}%"
        query = query.filter(or_(Document.title.ilike(like), Document.description.ilike(like), Document.original_filename.ilike(like), Document.category.ilike(like)))
    documents = query.order_by(Document.deleted_at.isnot(None), Document.document_date.desc().nullslast(), Document.uploaded_at.desc()).all()

    events = BowlingEvent.query.order_by(BowlingEvent.event_date.desc()).limit(50).all()
    cashbook_entries = CashbookEntry.query.filter(CashbookEntry.is_void.is_(False)).order_by(CashbookEntry.booking_date.desc(), CashbookEntry.id.desc()).limit(80).all()

    return render_template(
        "documents.html",
        documents=documents,
        categories=document_categories(),
        category_filter=category_filter,
        search=search,
        show_archived=show_archived,
        events=events,
        cashbook_entries=cashbook_entries,
    )


@app.route("/documents/download/<int:document_id>")
@login_required
@role_required("admin", "cashier", "auditor")
def document_download(document_id):
    doc = Document.query.get_or_404(document_id)
    path = DOCUMENT_DIR / doc.stored_filename
    if not path.exists():
        flash("Die Datei wurde auf dem Server nicht gefunden.", "error")
        return redirect(url_for("documents_page"))
    audit_log(
        "Dokumente",
        "document_downloaded",
        f"Dokument heruntergeladen: {doc.title}",
        details=document_summary(doc),
        object_type="document",
        object_id=doc.id,
    )
    return send_file(path, as_attachment=True, download_name=doc.original_filename)


@app.route("/documents/preview/<int:document_id>")
@login_required
@role_required("admin", "cashier", "auditor")
def document_preview(document_id):
    doc = Document.query.get_or_404(document_id)
    path = DOCUMENT_DIR / doc.stored_filename
    if not path.exists():
        flash("Die Datei wurde auf dem Server nicht gefunden.", "error")
        return redirect(url_for("documents_page"))

    preview_type = document_preview_type(doc)
    if preview_type == "download_only":
        flash("Für diesen Dateityp gibt es aktuell keine direkte Vorschau. Bitte die Datei herunterladen.", "info")
        return redirect(url_for("documents_page"))

    audit_log(
        "Dokumente",
        "document_previewed",
        f"Dokument-Vorschau geöffnet: {doc.title}",
        details=document_summary(doc),
        object_type="document",
        object_id=doc.id,
    )
    return send_file(path, as_attachment=False, download_name=doc.original_filename)


@app.route("/backups", methods=["GET", "POST"])
@login_required
@role_required("admin")
def backups_page():
    if request.method == "POST":
        actions = request.form.getlist("form_action")
        action = actions[-1] if actions else ""
        filename = secure_filename(request.form.get("filename", ""))
        redirect_target = request.form.get("redirect_to", "backups")
        redirect_endpoint = "import_export_page" if redirect_target == "import_export" else "backups_page"
        try:
            if action == "create_backup":
                backup = create_database_backup(kind="manual")
                audit_log(
                    "Datensicherung",
                    "backup_created",
                    "Manuelle Sicherung erstellt",
                    details=f"Sicherung erstellt: {backup.name}",
                    object_type="Backup",
                    new_value=backup.name,
                )
                db.session.commit()
                flash("Sicherung wurde erstellt.", "success")

            elif action == "upload_backup":
                uploaded = request.files.get("backup_upload")
                if not uploaded or not uploaded.filename:
                    flash("Bitte eine Backup-ZIP auswählen.", "danger")
                else:
                    original_name = secure_filename(uploaded.filename)
                    if not original_name.lower().endswith(".zip"):
                        flash("Bitte nur ZIP-Sicherungen hochladen.", "danger")
                    else:
                        folder = backup_dir()
                        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                        target_name = f"kegelkasse_backup_import_{stamp}_{original_name}"
                        target = folder / target_name
                        uploaded.save(target)
                        try:
                            check_summary = verify_backup_file(target)
                        except Exception:
                            try:
                                target.unlink()
                            except FileNotFoundError:
                                pass
                            raise
                        audit_log(
                            "Datensicherung",
                            "backup_uploaded",
                            "Sicherung hochgeladen",
                            details=f"Sicherung hochgeladen und geprüft: {target_name}. Ergebnis: {check_summary}",
                            object_type="Backup",
                            new_value=target_name,
                        )
                        db.session.commit()
                        flash(f"Sicherung wurde hochgeladen und geprüft: {check_summary}", "success")

            elif action == "upload_club_import":
                uploaded = request.files.get("club_import_upload")
                if not uploaded or not uploaded.filename:
                    flash("Bitte einen Vereins-Export als ZIP auswählen.", "danger")
                else:
                    original_name = secure_filename(uploaded.filename)
                    if not original_name.lower().endswith(".zip"):
                        flash("Bitte nur ZIP-Dateien hochladen.", "danger")
                    else:
                        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                        target_name = f"kegelkasse_club_import_{stamp}_{original_name}"
                        target = club_import_dir() / target_name
                        uploaded.save(target)
                        preview = validate_club_export_zip(target)
                        if not preview.get("ok"):
                            try:
                                target.unlink()
                            except FileNotFoundError:
                                pass
                            flash("Vereins-Export ist ungültig: " + "; ".join(preview.get("errors", [])), "danger")
                        else:
                            set_setting_value("club_import_pending_file", target.name)
                            set_setting_value("club_import_preview_json", json.dumps(preview, ensure_ascii=False))
                            db.session.commit()
                            members = preview.get("counts", {}).get("member", "?")
                            events = preview.get("counts", {}).get("bowling_event", "?")
                            docs = preview.get("documents_count", 0)
                            flash(f"Vereins-Export geprüft: {members} Mitglieder, {events} Kegelabende, {docs} Dokumentdateien erkannt. Bitte Import unten bestätigen.", "success")

            elif action == "execute_club_import":
                confirm = (request.form.get("confirm_club_import") or "").strip().upper()
                if confirm != "IMPORTIEREN":
                    flash("Import nicht gestartet. Bitte zur Bestätigung IMPORTIEREN eingeben.", "danger")
                else:
                    pending_name = secure_filename(setting_value("club_import_pending_file", "") or "")
                    pending_path = club_import_dir() / pending_name
                    if not pending_name or not pending_path.exists() or pending_path.parent != club_import_dir():
                        flash("Kein geprüfter Vereins-Export für den Import gefunden.", "danger")
                    else:
                        restore_before = import_club_export_zip(pending_path)
                        set_setting_value("club_import_pending_file", "")
                        set_setting_value("club_import_preview_json", "")
                        db.session.commit()
                        flash(f"Vereins-Export wurde importiert. Vorherige Installation wurde gesichert als: {restore_before.name}. Bitte neu anmelden.", "success")
                        return redirect(url_for("logout"))

            elif action == "cancel_club_import":
                pending_name = secure_filename(setting_value("club_import_pending_file", "") or "")
                pending_path = club_import_dir() / pending_name
                if pending_name and pending_path.exists() and pending_path.parent == club_import_dir():
                    try:
                        pending_path.unlink()
                    except FileNotFoundError:
                        pass
                set_setting_value("club_import_pending_file", "")
                set_setting_value("club_import_preview_json", "")
                db.session.commit()
                flash("Vereins-Import wurde verworfen.", "info")

            elif action == "backup_settings":
                old_summary = f"Aktiv: {audit_value(setting_value('backup_enabled', '0'))}; Intervall: {audit_value(setting_value('backup_interval', 'daily'))}; Uhrzeit: {audit_value(setting_value('backup_time', '02:00'))}; Aufbewahrung: {audit_value(setting_value('backup_keep_days', '30'))} Tage; Generationen: {audit_value(setting_value('backup_keep_count', '20'))}; Ziel: {audit_value(backup_target_label())}"
                set_setting_value("backup_enabled", "1" if request.form.get("backup_enabled") else "0")
                set_setting_value("backup_interval", request.form.get("backup_interval", "daily"))
                set_setting_value("backup_time", request.form.get("backup_time", "02:00") or "02:00")
                set_setting_value("backup_keep_days", request.form.get("backup_keep_days", "30") or "30")
                set_setting_value("backup_keep_count", request.form.get("backup_keep_count", "20") or "20")
                set_setting_value("backup_include_documents", "1" if request.form.get("backup_include_documents") else "0")
                set_setting_value("backup_extra_target_enabled", "1" if request.form.get("backup_extra_target_enabled") else "0")
                target_type = request.form.get("backup_target_type", "webdav")
                if target_type not in ("webdav", "dropbox", "onedrive", "google_drive"):
                    target_type = "webdav"
                set_setting_value("backup_target_type", target_type)
                set_setting_value("backup_webdav_url", request.form.get("backup_webdav_url", "").strip())
                set_setting_value("backup_webdav_username", request.form.get("backup_webdav_username", "").strip())
                webdav_password = request.form.get("backup_webdav_password", "")
                if webdav_password:
                    set_setting_value("backup_webdav_password", webdav_password)
                if request.form.get("backup_webdav_password_clear"):
                    set_setting_value("backup_webdav_password", "")
                new_summary = f"Aktiv: {audit_value(setting_value('backup_enabled', '0'))}; Intervall: {audit_value(setting_value('backup_interval', 'daily'))}; Uhrzeit: {audit_value(setting_value('backup_time', '02:00'))}; Aufbewahrung: {audit_value(setting_value('backup_keep_days', '30'))} Tage; Generationen: {audit_value(setting_value('backup_keep_count', '20'))}; Ziel: {audit_value(backup_target_label())}"
                audit_log(
                    "Datensicherung",
                    "backup_settings_updated",
                    "Sicherungseinstellungen geändert",
                    details="Einstellungen für automatische Sicherungen wurden aktualisiert.",
                    object_type="AppSetting",
                    old_value=old_summary,
                    new_value=new_summary,
                )
                db.session.commit()
                cleanup_old_backups()
                flash("Sicherungseinstellungen wurden gespeichert.", "success")

            elif action == "test_backup_target":
                test_file = backup_dir() / "kegelkasse_backup_test.txt"
                test_file.write_text(f"Kegelkasse Backup-Test\n{datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")
                try:
                    copied_to = copy_backup_to_extra_target(test_file)
                    set_setting_value("backup_last_result", f"Test OK: {copied_to}")
                    audit_log(
                        "Datensicherung",
                        "backup_target_tested",
                        "Backup-Ziel getestet",
                        details=f"Backup-Ziel erfolgreich getestet: {copied_to}",
                        object_type="Backup",
                        new_value=str(copied_to),
                    )
                    db.session.commit()
                    flash("Backup-Ziel wurde erfolgreich getestet.", "success")
                finally:
                    try:
                        test_file.unlink()
                    except FileNotFoundError:
                        pass

            elif action == "delete_backup":
                path = backup_dir() / filename
                if not filename or not path.exists() or path.parent != backup_dir():
                    flash("Sicherungsdatei wurde nicht gefunden.", "danger")
                else:
                    path.unlink()
                    audit_log(
                        "Datensicherung",
                        "backup_deleted",
                        "Sicherung gelöscht",
                        details=f"Sicherung gelöscht: {filename}",
                        object_type="Backup",
                        old_value=filename,
                    )
                    db.session.commit()
                    flash("Sicherung wurde gelöscht.", "success")

            elif action == "check_backup":
                path = backup_dir() / filename
                check_summary = verify_backup_file(path)
                audit_log(
                    "Datensicherung",
                    "backup_checked",
                    "Sicherung geprüft",
                    details=f"Sicherung geprüft: {filename}. Ergebnis: {check_summary}",
                    object_type="Backup",
                    new_value=filename,
                )
                db.session.commit()
                flash(f"Sicherung ist lesbar: {check_summary}", "success")


            elif action == "restore_backup":
                confirm = request.form.get("confirm_restore", "").strip().upper()
                if confirm != "WIEDERHERSTELLEN":
                    flash("Bitte zur Sicherheit WIEDERHERSTELLEN eingeben.", "danger")
                else:
                    path = backup_dir() / filename
                    restore_before = restore_database_from_backup(path)
                    audit_log(
                        "Datensicherung",
                        "backup_restored",
                        "Sicherung wiederhergestellt",
                        details=f"Sicherung wiederhergestellt: {filename}. Vorher wurde automatisch eine Sicherung erstellt: {restore_before.name}",
                        object_type="Backup",
                        old_value=restore_before.name,
                        new_value=filename,
                    )
                    db.session.commit()
                    flash("Sicherung wurde wiederhergestellt. Bitte Anwendung neu laden.", "success")

        except Exception as exc:
            db.session.rollback()
            flash(f"Aktion konnte nicht ausgeführt werden: {exc}", "danger")
        return redirect(url_for(redirect_endpoint))

    files = backup_file_list()
    last_manual = next((item for item in files if item["kind"] == "Manuell"), None)
    last_auto = next((item for item in files if item["kind"] == "Automatisch"), None)
    club_import_preview = None
    preview_raw = setting_value("club_import_preview_json", "")
    if preview_raw:
        try:
            club_import_preview = json.loads(preview_raw)
        except Exception:
            club_import_preview = None
    return render_template(
        "backups.html",
        files=files,
        settings=backup_settings(),
        db_info=database_info(),
        last_manual=last_manual,
        last_auto=last_auto,
        club_import_preview=club_import_preview,
    )



def club_import_preview_from_settings():
    preview_raw = setting_value("club_import_preview_json", "")
    if not preview_raw:
        return None
    try:
        preview = json.loads(preview_raw)
        return preview if isinstance(preview, dict) else None
    except Exception:
        return None


@app.route("/import-export")
@login_required
@role_required("admin")
def import_export_page():
    return render_template(
        "import_export.html",
        years=export_year_options(),
        current_year=datetime.now().year,
        club_import_preview=club_import_preview_from_settings(),
    )


@app.route("/club/export/download")
@login_required
@role_required("admin")
def download_club_export():
    include_documents = request.args.get("include_documents", "1") == "1"
    try:
        export_path = create_club_export(include_documents=include_documents)
        audit_log(
            "Vereins-Export",
            "club_export_created",
            "Vereins-Export erstellt",
            details=f"Vereins-Export erstellt: {export_path.name}",
            object_type="ClubExport",
            new_value=export_path.name,
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Vereins-Export konnte nicht erstellt werden: {exc}", "danger")
        return redirect(url_for("backups_page"))

    @after_this_request
    def cleanup_export(response):
        try:
            export_path.unlink()
        except Exception:
            pass
        return response

    return send_file(export_path, as_attachment=True, download_name=export_path.name)


@app.route("/backups/download/<path:filename>")
@login_required
@role_required("admin")
def download_backup(filename):
    safe_name = secure_filename(filename)
    path = backup_dir() / safe_name
    if not safe_name or not path.exists() or path.parent != backup_dir():
        flash("Sicherungsdatei wurde nicht gefunden.", "danger")
        return redirect(url_for("backups_page"))
    return send_file(path, as_attachment=True, download_name=safe_name)


@app.route("/members")
@login_required
def members():
    members_list = Member.query.order_by(
        Member.active.desc(),
        Member.first_name,
        Member.last_name,
    ).all()

    return render_template("members.html", members=members_list)


@app.route("/members/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier")
def member_new():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        nickname = request.form.get("nickname", "").strip()
        email = request.form.get("email", "").strip()
        joined_at_raw = request.form.get("joined_at", "").strip()
        note = request.form.get("note", "").strip()

        if not first_name:
            flash("Vorname ist ein Pflichtfeld.", "danger")
            return redirect(url_for("member_new"))

        joined_at = (
            datetime.strptime(joined_at_raw, "%Y-%m-%d").date()
            if joined_at_raw
            else datetime.today().date()
        )

        member = Member(
            first_name=first_name,
            last_name=last_name,
            nickname=nickname or None,
            email=email or None,
            joined_at=joined_at,
            note=note or None,
            active=True,
        )

        db.session.add(member)
        db.session.flush()

        update_member_login(member)

        audit_log(
            "member",
            "member_created",
            f"Mitglied {member.display_name()} angelegt",
            details="Neues Mitglied angelegt.",
            object_type="Member",
            object_id=member.id,
            new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in member_audit_snapshot(member).items()),
        )
        db.session.commit()

        flash("Mitglied wurde angelegt.", "success")
        return redirect(url_for("members"))

    return render_template(
        "member_form.html",
        member=None,
        today=datetime.today().date().isoformat(),
    )


@app.route("/members/<int:member_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier")
def member_edit(member_id):
    member = Member.query.get_or_404(member_id)

    if request.method == "POST":
        old_snapshot = member_audit_snapshot(member)

        member.first_name = request.form.get("first_name", "").strip()
        member.last_name = request.form.get("last_name", "").strip()
        member.nickname = request.form.get("nickname", "").strip() or None
        member.email = request.form.get("email", "").strip() or None
        member.note = request.form.get("note", "").strip() or None
        member.active = request.form.get("active") == "on"

        joined_at_raw = request.form.get("joined_at", "").strip()
        left_at_raw = request.form.get("left_at", "").strip()

        member.joined_at = (
            datetime.strptime(joined_at_raw, "%Y-%m-%d").date()
            if joined_at_raw
            else None
        )

        member.left_at = (
            datetime.strptime(left_at_raw, "%Y-%m-%d").date()
            if left_at_raw
            else None
        )

        update_member_login(member)

        new_snapshot = member_audit_snapshot(member)
        audit_log(
            "member",
            "member_saved",
            f"Mitglied {member.display_name()} gespeichert",
            details=audit_diff_lines(old_snapshot, new_snapshot),
            object_type="Member",
            object_id=member.id,
            old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
            new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
        )
        db.session.commit()

        flash("Mitglied wurde gespeichert.", "success")
        return redirect(url_for("members"))

    return render_template(
        "member_form.html",
        member=member,
        today=datetime.today().date().isoformat(),
    )


@app.route("/members/<int:member_id>/delete", methods=["POST"])
@login_required
@role_required("admin", "cashier")
def member_delete(member_id):
    member = Member.query.get_or_404(member_id)

    if member.user:
        db.session.delete(member.user)

    member_name = member.display_name()
    db.session.delete(member)
    audit_log(
        "member",
        "member_deleted",
        f"Mitglied {member_name} gelöscht",
        details=f"Mitglied gelöscht: {member_name}",
        object_type="Member",
        object_id=member_id,
    )
    db.session.commit()

    flash("Mitglied wurde gelöscht.", "success")
    return redirect(url_for("members"))


@app.route("/settings/rates")
@login_required
def rate_settings():
    all_rates = RateSetting.query.order_by(
        RateSetting.key,
        RateSetting.valid_from.desc(),
    ).all()

    rate_groups = []
    for key in BASE_RATE_KEYS:
        label = RATE_TYPES[key]
        history = [rate for rate in all_rates if rate.key == key]
        if history:
            rate_groups.append({
                "key": key,
                "label": label,
                "current": history[0],
                "history": history,
            })

    return render_template(
        "rate_settings.html",
        rate_groups=rate_groups,
        rate_types={key: RATE_TYPES[key] for key in BASE_RATE_KEYS},
        event_rhythm_type=setting_value("event_rhythm_type", "weeks"),
        event_rhythm_weeks=setting_value("event_rhythm_weeks", "4"),
        event_rhythm_weekday=setting_value("event_rhythm_weekday", "4"),
        event_rhythm_nth=setting_value("event_rhythm_nth", "1"),
        event_rhythm_month_day=setting_value("event_rhythm_month_day", "1"),
    )



@app.route("/settings/event-rhythm")
@login_required
@role_required("admin", "cashier")
def event_rhythm_page():
    return render_template(
        "event_rhythm.html",
        event_rhythm_type=setting_value("event_rhythm_type", "weeks"),
        event_rhythm_weeks=setting_value("event_rhythm_weeks", "4"),
        event_rhythm_weekday=setting_value("event_rhythm_weekday", "4"),
        event_rhythm_nth=setting_value("event_rhythm_nth", "1"),
        event_rhythm_month_day=setting_value("event_rhythm_month_day", "1"),
    )


@app.route("/settings/event-rhythm", methods=["POST"])
@login_required
@role_required("admin", "cashier")
def event_rhythm_settings():
    rhythm_type = request.form.get("event_rhythm_type", "weeks")
    allowed = ["weeks", "monthly_nth_weekday", "monthly_last_weekday", "monthly_day"]
    if rhythm_type == "monthly_first_weekday":
        rhythm_type = "monthly_nth_weekday"
    if rhythm_type not in allowed:
        rhythm_type = "weeks"

    try:
        weeks = max(1, int(request.form.get("event_rhythm_weeks", "4")))
    except ValueError:
        weeks = 4

    try:
        weekday = int(request.form.get("event_rhythm_weekday", "4"))
    except ValueError:
        weekday = 4
    weekday = min(max(weekday, 0), 6)

    try:
        nth = int(request.form.get("event_rhythm_nth", "1"))
    except ValueError:
        nth = 1
    nth = min(max(nth, 1), 4)

    try:
        month_day = int(request.form.get("event_rhythm_month_day", "1"))
    except ValueError:
        month_day = 1
    month_day = min(max(month_day, 1), 31)

    set_setting_value("event_rhythm_type", rhythm_type)
    set_setting_value("event_rhythm_weeks", weeks)
    set_setting_value("event_rhythm_weekday", weekday)
    set_setting_value("event_rhythm_nth", nth)
    set_setting_value("event_rhythm_month_day", month_day)
    audit_log(
        "settings",
        "event_rhythm_saved",
        "Kegelabend-Rhythmus gespeichert",
        new_value=f"Typ={rhythm_type}, Wochen={weeks}, Wochentag={weekday}, n={nth}, Monatstag={month_day}",
    )
    db.session.commit()
    flash("Kegelabend-Rhythmus wurde gespeichert.", "success")
    return redirect(url_for("event_rhythm_page"))

@app.route("/settings/rates/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def rate_new():
    if request.method == "POST":
        key = request.form.get("key", "").strip()
        amount = request.form.get("amount", "").strip()
        valid_from_raw = request.form.get("valid_from", "").strip()
        note = request.form.get("note", "").strip()

        if key not in BASE_RATE_KEYS:
            flash("Ungültiger Einstellungstyp. Strafarten bitte im Modul Strafarten pflegen.", "danger")
            return redirect(url_for("rate_new"))

        try:
            amount_cents = form_euro_to_cents("amount", "Betrag")
        except ValueError:
            return redirect(url_for("rate_new"))

        rate = RateSetting(
            key=key,
            label=RATE_TYPES[key],
            amount_cents=amount_cents,
            valid_from=datetime.strptime(valid_from_raw, "%Y-%m-%d").date(),
            active=True,
            note=note or None,
        )

        db.session.add(rate)
        audit_log(
            "settings",
            "rate_created",
            f"Einstellung {RATE_TYPES[key]} angelegt",
            new_value=f"{cents_to_euro(amount_cents)} € ab {valid_from_raw}",
        )
        db.session.commit()

        flash("Neuer Wert wurde mit Gültigkeitsdatum angelegt.", "success")
        return redirect(url_for("rate_settings"))

    return render_template(
        "rate_form.html",
        rate_types={key: RATE_TYPES[key] for key in BASE_RATE_KEYS},
        today=datetime.today().date().isoformat(),
    )


@app.route("/settings/penalty-types")
@login_required
@role_required("admin", "cashier")
def penalty_types():
    types = PenaltyType.query.order_by(PenaltyType.sort_order, PenaltyType.name).all()
    return render_template("penalty_types.html", penalty_types=types)


@app.route("/settings/penalty-types/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier")
def penalty_type_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        kind = request.form.get("kind", "count").strip()
        amount_raw = request.form.get("amount", "0").strip()
        target_mode = request.form.get("target_mode", "self").strip()
        sort_order = int(request.form.get("sort_order", 100) or 100)

        if not name:
            flash("Bitte einen Namen für die Strafart eingeben.", "danger")
            return redirect(url_for("penalty_type_new"))

        if kind not in ("count", "amount"):
            flash("Ungültiger Strafart-Typ.", "danger")
            return redirect(url_for("penalty_type_new"))

        if target_mode not in ("self", "others"):
            flash("Bitte eine gültige Berechnung auswählen.", "danger")
            return redirect(url_for("penalty_type_new"))

        try:
            amount_cents = form_euro_to_cents("amount", "Betrag")
        except ValueError:
            return redirect(url_for("penalty_type_new"))

        penalty_type = PenaltyType(
            name=name,
            key=None,
            kind=kind,
            amount_cents=amount_cents,
            active=request.form.get("active") == "on",
            sort_order=sort_order,
            target_mode=target_mode,
        )
        db.session.add(penalty_type)
        db.session.flush()
        audit_log(
            "penalty_type",
            "penalty_type_created",
            f"Strafart {penalty_type.name} angelegt",
            object_type="PenaltyType",
            object_id=penalty_type.id,
            details="Neue Strafart angelegt.",
            new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in penalty_type_audit_snapshot(penalty_type).items()),
        )
        db.session.commit()

        flash("Strafart wurde angelegt.", "success")
        return redirect(url_for("penalty_types"))

    return render_template("penalty_type_form.html", penalty_type=None)


@app.route("/settings/penalty-types/<int:type_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier")
def penalty_type_edit(type_id):
    penalty_type = PenaltyType.query.get_or_404(type_id)

    if request.method == "POST":
        old_snapshot = penalty_type_audit_snapshot(penalty_type)
        penalty_type.name = request.form.get("name", "").strip()
        penalty_type.kind = request.form.get("kind", "count").strip()
        penalty_type.target_mode = request.form.get("target_mode", "self").strip()
        try:
            penalty_type.amount_cents = form_euro_to_cents("amount", "Betrag")
        except ValueError:
            return redirect(url_for("penalty_type_edit", type_id=penalty_type.id))
        penalty_type.active = request.form.get("active") == "on"
        penalty_type.sort_order = int(request.form.get("sort_order", 100) or 100)

        if not penalty_type.name:
            flash("Bitte einen Namen für die Strafart eingeben.", "danger")
            return redirect(url_for("penalty_type_edit", type_id=penalty_type.id))

        if penalty_type.kind not in ("count", "amount"):
            flash("Ungültiger Strafart-Typ.", "danger")
            return redirect(url_for("penalty_type_edit", type_id=penalty_type.id))

        new_snapshot = penalty_type_audit_snapshot(penalty_type)
        audit_log(
            "penalty_type",
            "penalty_type_saved",
            f"Strafart {penalty_type.name} gespeichert",
            details=audit_diff_lines(old_snapshot, new_snapshot),
            object_type="PenaltyType",
            object_id=penalty_type.id,
            old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
            new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
        )
        db.session.commit()
        flash("Strafart wurde gespeichert.", "success")
        return redirect(url_for("penalty_types"))

    return render_template("penalty_type_form.html", penalty_type=penalty_type)


@app.route("/settings/penalty-types/<int:type_id>/delete", methods=["POST"])
@login_required
@role_required("admin", "cashier")
def penalty_type_delete(type_id):
    penalty_type = PenaltyType.query.get_or_404(type_id)
    used = ParticipantPenalty.query.filter_by(penalty_type_id=penalty_type.id).first()

    if used:
        penalty_type.active = False
        audit_log(
            "penalty_type",
            "penalty_type_deactivated",
            f"Strafart {penalty_type.name} deaktiviert",
            details="Strafart wurde bereits verwendet und deshalb deaktiviert statt gelöscht.",
            object_type="PenaltyType",
            object_id=penalty_type.id,
            new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in penalty_type_audit_snapshot(penalty_type).items()),
        )
        db.session.commit()
        flash("Strafart wurde bereits verwendet und deshalb nur deaktiviert.", "success")
    else:
        penalty_name = penalty_type.name
        db.session.delete(penalty_type)
        audit_log(
            "penalty_type",
            "penalty_type_deleted",
            f"Strafart {penalty_name} gelöscht",
            details=f"Strafart gelöscht: {penalty_name}",
            object_type="PenaltyType",
            object_id=type_id,
        )
        db.session.commit()
        flash("Strafart wurde gelöscht.", "success")

    return redirect(url_for("penalty_types"))


@app.route("/cashbook/opening-balances", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier")
def opening_balances():
    if request.method == "POST":
        cash_amount = request.form.get("cash_amount", "").strip()
        bank_amount = request.form.get("bank_amount", "").strip()
        booking_date_raw = request.form.get("booking_date", "").strip()
        description = request.form.get("description", "").strip()

        booking_date = datetime.strptime(booking_date_raw, "%Y-%m-%d").date()

        try:
            cash_cents = form_euro_to_cents("cash_amount", "Barkasse")
            bank_cents = form_euro_to_cents("bank_amount", "Bankkonto")
        except ValueError:
            return redirect(url_for("opening_balances"))

        AccountTransaction.query.filter_by(category="opening_balance").delete()

        db.session.add(AccountTransaction(
            account="cash",
            category="opening_balance",
            amount_cents=cash_cents,
            booking_date=booking_date,
            description=description or "Startbestand Barkasse",
        ))

        db.session.add(AccountTransaction(
            account="bank",
            category="opening_balance",
            amount_cents=bank_cents,
            booking_date=booking_date,
            description=description or "Startbestand Bankkonto",
        ))

        db.session.commit()

        flash("Startbestände wurden gespeichert.", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "opening_balances.html",
        today=datetime.today().date().isoformat(),
        cash_balance=cents_to_euro(account_balance("cash")),
        bank_balance=cents_to_euro(account_balance("bank")),
    )




def active_penalty_types():
    return PenaltyType.query.filter_by(active=True).order_by(
        PenaltyType.sort_order,
        PenaltyType.name,
    ).all()


def get_or_create_participant_penalty(participant, penalty_type):
    penalty = ParticipantPenalty.query.filter_by(
        participant_id=participant.id,
        penalty_type_id=penalty_type.id,
    ).first()

    if not penalty:
        penalty = ParticipantPenalty(
            participant_id=participant.id,
            penalty_type_id=penalty_type.id,
            quantity=0,
            amount_cents=0,
        )
        db.session.add(penalty)
        db.session.flush()

    return penalty


def participant_display_name(participant):
    """Anzeigename: Spitzname bevorzugt, sonst Vorname."""
    if participant.member:
        return participant.member.nickname or participant.member.first_name or participant.member.last_name

    return participant.guest_name or "Gast"


def participant_sort_name(participant):
    """Sortierung aktuell bewusst nach Vorname; später per Einstellung erweiterbar."""
    if participant.member:
        return (participant.member.first_name or participant.member.nickname or participant.member.last_name or "").casefold()

    return (participant.guest_name or "Gast").casefold()


def participant_sort_key(row):
    """Anwesende oben, Gäste danach, Fehlende unten, dann nach Vorname."""
    status_order = {
        "present": 0,
        "guest": 1,
        "excused": 2,
        "unexcused": 2,
    }

    participant = row["participant"]
    return (
        status_order.get(participant.status, 9),
        participant_sort_name(participant),
        row["display_name"].casefold(),
    )


def penalty_entry_amount_cents(penalty):
    if not penalty.penalty_type or not penalty.penalty_type.active:
        return 0
    if penalty.penalty_type.kind == "amount":
        return penalty.amount_cents or 0
    return (penalty.quantity or 0) * (penalty.penalty_type.amount_cents or 0)


def participant_base_penalty_cents(participant):
    """Nur echte Strafbeträge ohne Gastbeitrag und ohne Fehlgeld.

    target_mode self: Der Spieler zahlt die eigene Strafe.
    target_mode others: Alle anderen anwesenden Spieler/Gäste zahlen diese Strafe.
    """
    total = 0

    if participant.status not in ("present", "guest"):
        return 0

    # Eigene Strafen, die der Spieler selbst zahlt.
    for penalty in participant.dynamic_penalties:
        if getattr(penalty.penalty_type, "target_mode", "self") == "others":
            continue
        total += penalty_entry_amount_cents(penalty)

    # Strafen anderer Spieler, die alle anderen zahlen.
    others = EventParticipant.query.filter(
        EventParticipant.event_id == participant.event_id,
        EventParticipant.id != participant.id,
        EventParticipant.status.in_(("present", "guest")),
    ).all()

    for other in others:
        for penalty in other.dynamic_penalties:
            if getattr(penalty.penalty_type, "target_mode", "self") != "others":
                continue
            total += penalty_entry_amount_cents(penalty)

    return total


def event_average_present_penalty_raw_cents(event):
    """Ungerundete Durchschnittsstrafe der Anwesenden/Gäste, ohne Gastbeitrag."""
    present_participants = EventParticipant.query.filter(
        EventParticipant.event_id == event.id,
        EventParticipant.status.in_(("present", "guest")),
    ).all()

    if not present_participants:
        return 0

    total = sum(participant_base_penalty_cents(participant) for participant in present_participants)
    return int(round(total / len(present_participants)))


def event_average_present_penalty_cents(event):
    """Auf 0,10 € gerundete Durchschnittsstrafe der Anwesenden/Gäste, ohne Gastbeitrag."""
    return round_to_ten_cents(event_average_present_penalty_raw_cents(event))


def participant_penalty_cents(participant, target_date, average_present_cents=0):
    """Berechnet Strafen/Gebühren für einen Teilnehmer in Cent."""
    total = 0

    if participant.status == "guest":
        total += current_rate_cents("guest_fee", target_date)
        total += participant_base_penalty_cents(participant)

    elif participant.status == "present":
        total += participant_base_penalty_cents(participant)

    elif participant.status == "excused":
        total += average_present_cents
        total += current_rate_cents("absence_excused", target_date)

    elif participant.status == "unexcused":
        total += average_present_cents
        total += current_rate_cents("absence_unexcused", target_date)

    return total


def participant_penalty_breakdown(participant):
    """Aufteilung der sichtbaren Strafwerte für die persönliche Nur-Lese-Ansicht.

    Bei Strafarten mit target_mode="others" wird getrennt angezeigt:
    - wie oft das Mitglied selbst das Ereignis ausgelöst/geworfen hat
    - wie oft es bezahlen muss, weil andere Spieler das Ereignis ausgelöst haben
    """
    if not participant:
        return []

    items = []
    penalty_types = active_penalty_types()

    for penalty_type in penalty_types:
        target_mode = getattr(penalty_type, "target_mode", "self")
        cents = 0
        quantity = 0
        own_quantity = 0
        payable_quantity = 0
        note = None

        if target_mode == "others":
            own_penalty = ParticipantPenalty.query.filter_by(
                participant_id=participant.id,
                penalty_type_id=penalty_type.id,
            ).first()
            if own_penalty:
                own_quantity = own_penalty.quantity or 0
                if own_penalty.note:
                    note = own_penalty.note

            others = EventParticipant.query.filter(
                EventParticipant.event_id == participant.event_id,
                EventParticipant.id != participant.id,
                EventParticipant.status.in_(("present", "guest")),
            ).all()
            for other in others:
                penalty = ParticipantPenalty.query.filter_by(
                    participant_id=other.id,
                    penalty_type_id=penalty_type.id,
                ).first()
                if penalty:
                    cents += penalty_entry_amount_cents(penalty)
                    payable_quantity += penalty.quantity or 0
                    if penalty.note and not note:
                        note = penalty.note

            quantity = payable_quantity
        else:
            penalty = ParticipantPenalty.query.filter_by(
                participant_id=participant.id,
                penalty_type_id=penalty_type.id,
            ).first()
            if penalty:
                cents += penalty_entry_amount_cents(penalty)
                quantity += penalty.quantity or 0
                note = penalty.note

        items.append({
            "name": penalty_type.name,
            "kind": penalty_type.kind,
            "target_mode": target_mode,
            "quantity": quantity,
            "own_quantity": own_quantity,
            "payable_quantity": payable_quantity,
            "amount_cents": cents,
            "amount_euro": cents_to_euro(cents),
            "note": note,
        })

    return items


def current_member_for_user():
    if not current_user.is_authenticated:
        return None
    return Member.query.filter_by(user_id=current_user.id).first()


def personal_event_payload():
    member = current_member_for_user()
    if not member:
        return {
            "has_member": False,
            "message": "Dein Benutzerkonto ist noch keinem Mitglied zugeordnet.",
        }

    event = BowlingEvent.query.filter(BowlingEvent.status.in_(("open", "settlement", "lane_cost"))).order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc()).first()
    is_live = True

    if not event:
        event = BowlingEvent.query.filter(BowlingEvent.status == "closed").order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc()).first()
        is_live = False

    if not event:
        return {
            "has_member": True,
            "has_event": False,
            "message": "Es wurde noch kein Kegelabend gefunden.",
        }

    participant = EventParticipant.query.filter_by(event_id=event.id, member_id=member.id).first()
    if not participant:
        return {
            "has_member": True,
            "has_event": True,
            "event_date": event.event_date.strftime("%d.%m.%Y"),
            "is_live": is_live,
            "message": "Für dich wurde bei diesem Kegelabend kein Eintrag gefunden.",
        }

    average_present_cents = event_average_present_penalty_cents(event)
    total_cents = participant_penalty_cents(participant, event.event_date, average_present_cents)
    stored_balance_cents = member_penalty_balance(member.id)
    # Bei laufenden Abenden sind die Strafwerte noch nicht endgültig im Strafkonto gebucht.
    # Für die Mitgliederansicht zeigen wir deshalb live: bisher offen + aktueller Abend.
    balance_cents = stored_balance_cents + total_cents if is_live and event.status == "open" else stored_balance_cents
    lock = EventEditLock.query.filter_by(event_id=event.id).first() if is_live else None
    editor = lock.user.username if lock and lock.user else None

    return {
        "has_member": True,
        "has_event": True,
        "event_id": event.id,
        "event_date": event.event_date.strftime("%d.%m.%Y"),
        "is_live": is_live,
        "editor": editor,
        "status": participant.status,
        "status_label": {
            "present": "Anwesend",
            "guest": "Gast",
            "excused": "Fehlt entschuldigt",
            "unexcused": "Fehlt unentschuldigt",
        }.get(participant.status, participant.status),
        "items": participant_penalty_breakdown(participant),
        "current_total_cents": total_cents,
        "current_total_euro": cents_to_euro(total_cents),
        "balance_cents": balance_cents,
        "balance_euro": cents_to_euro(balance_cents),
        "message": None,
    }


def member_penalty_balance(member_id):
    transactions = MemberPenaltyTransaction.query.filter_by(member_id=member_id).all()
    return sum(transaction.amount_cents for transaction in transactions)

def get_app_setting(key, default=None):
    setting = AppSetting.query.filter_by(key=key).first()
    if setting is None or setting.value is None:
        return default
    return setting.value


def get_finance_settings():
    return {
        "capital_tax_enabled": get_app_setting("tax_capital_enabled", "1") == "1",
        "capital_tax_rate": float(
            get_app_setting("tax_capital_rate", "25,00").replace(",", ".")
        ),

        "solidarity_enabled": get_app_setting("tax_solidarity_enabled", "1") == "1",
        "solidarity_rate": float(
            get_app_setting("tax_solidarity_rate", "5,50").replace(",", ".")
        ),

        "church_enabled": get_app_setting("tax_church_enabled", "0") == "1",
        "church_rate": float(
            get_app_setting("tax_church_rate", "0,00").replace(",", ".")
        ),

        "calculation_mode": get_app_setting(
            "tax_calculation_mode",
            "auto",
        ),

        "rounding_mode": get_app_setting(
            "tax_rounding_mode",
            "commercial",
        ),
    }

def calculate_interest_taxes(gross_interest_cents):
    finance = get_finance_settings()

    gross_interest_cents = gross_interest_cents or 0

    capital_tax = 0
    solidarity_tax = 0
    church_tax = 0

    if finance["capital_tax_enabled"]:
        capital_tax = round(
            gross_interest_cents * finance["capital_tax_rate"] / 100
        )

    if finance["solidarity_enabled"]:
        solidarity_tax = round(
            capital_tax * finance["solidarity_rate"] / 100
        )

    if finance["church_enabled"]:
        church_tax = round(
            capital_tax * finance["church_rate"] / 100
        )

    net_interest = (
        gross_interest_cents
        - capital_tax
        - solidarity_tax
        - church_tax
    )

    return {
        "gross": gross_interest_cents,
        "capital_tax": capital_tax,
        "solidarity_tax": solidarity_tax,
        "church_tax": church_tax,
        "net": net_interest,
    }

def can_edit_closed_events():
    return current_user.role in ("admin", "cashier")


def can_write_running_events():
    return current_user.role in ("admin", "cashier", "member")


def can_override_event_lock():
    return current_user.role in ("admin", "cashier")


def round_to_ten_cents(cents):
    """Mathematisch auf den nächsten 0,10-Euro-Schritt runden."""
    cents = cents or 0
    if cents <= 0:
        return 0
    return int(round(cents / 10) * 10)


def acquire_event_lock(event):
    """Sperrt einen laufenden Kegelabend kurzzeitig für einen bearbeitenden Benutzer.

    Die Sperre wird bei jedem Laden/Speichern verlängert. Läuft der Browser weg
    oder wird geschlossen, verfällt die Sperre automatisch nach wenigen Minuten.
    """
    if event.status in ("closed", "cancelled"):
        return True, None

    if not can_write_running_events():
        return False, None

    now = datetime.utcnow()
    locked_until = now + timedelta(minutes=5)

    lock = EventEditLock.query.filter_by(event_id=event.id).first()

    if lock and lock.locked_until < now:
        db.session.delete(lock)
        db.session.commit()
        lock = None

    if lock and lock.user_id != current_user.id:
        return False, lock

    if not lock:
        lock = EventEditLock(
            event_id=event.id,
            user_id=current_user.id,
            locked_at=now,
            locked_until=locked_until,
        )
        db.session.add(lock)
    else:
        lock.locked_at = now
        lock.locked_until = locked_until

    db.session.commit()
    return True, lock


def release_event_lock(event):
    EventEditLock.query.filter_by(
        event_id=event.id,
        user_id=current_user.id,
    ).delete()




@app.route("/events/<int:event_id>/lock/ping", methods=["POST"])
@login_required
def event_lock_ping(event_id):
    event = BowlingEvent.query.get_or_404(event_id)
    lock_allowed, active_lock = acquire_event_lock(event)
    if not lock_allowed:
        editor = active_lock.user.username if active_lock and active_lock.user else "anderem Benutzer"
        return jsonify({
            "ok": False,
            "message": f"Dieser Kegelabend wird gerade von {editor} bearbeitet.",
            "editor": editor,
        }), 409
    return jsonify({"ok": True})


@app.route("/events/<int:event_id>/lock/release", methods=["POST"])
@login_required
def event_lock_release(event_id):
    event = BowlingEvent.query.get_or_404(event_id)
    release_event_lock(event)
    db.session.commit()
    return ("", 204)


def save_event_participants(event):
    participants = EventParticipant.query.filter_by(event_id=event.id).all()
    penalty_types = active_penalty_types()

    for participant in participants:
        prefix = f"participant_{participant.id}_"

        if participant.member_id:
            requested_status = request.form.get(prefix + "status", participant.status)
            if requested_status in ("present", "excused", "unexcused"):
                participant.status = requested_status
            else:
                participant.status = "present"
        else:
            participant.status = "guest"

        for penalty_type in penalty_types:
            penalty = get_or_create_participant_penalty(participant, penalty_type)
            field_prefix = f"penalty_{participant.id}_{penalty_type.id}"

            if penalty_type.kind == "amount":
                penalty.amount_cents = form_euro_to_cents(field_prefix + "_amount", penalty_type.name)
                penalty.quantity = 1 if penalty.amount_cents else 0
                penalty.note = request.form.get(field_prefix + "_note", "").strip() or None
            else:
                quantity_raw = request.form.get(field_prefix + "_quantity", "0") or "0"
                if not str(quantity_raw).isdigit():
                    flash(f"Bitte bei '{penalty_type.name}' nur ganze Zahlen eingeben.", "danger")
                    raise ValueError("Ungültige Anzahl")
                penalty.quantity = max(0, int(quantity_raw))
                penalty.amount_cents = 0
                penalty.note = None

def reset_event_bookings(event):
    MemberPenaltyTransaction.query.filter_by(event_id=event.id).delete()

    marker = f"Kegelabend #{event.id}"
    old_transactions = AccountTransaction.query.filter(
        AccountTransaction.description.like(f"%{marker}%")
    ).all()

    for transaction in old_transactions:
        db.session.delete(transaction)


def clear_event_entry_values(event):
    """Entfernt alle Erfassungswerte eines Abends, z. B. bei Ausfall."""
    participants = EventParticipant.query.filter_by(event_id=event.id).all()

    for participant in participants:
        if participant.member_id:
            participant.status = "present"
        else:
            participant.status = "guest"

        for penalty in participant.dynamic_penalties:
            penalty.quantity = 0
            penalty.amount_cents = 0
            penalty.note = None


def report_year_options():
    years = [row[0] for row in db.session.query(db.extract("year", BowlingEvent.event_date)).distinct().all() if row[0] is not None]
    years = sorted({int(year) for year in years}, reverse=True)
    return years


def report_event_query(selected_year):
    query = BowlingEvent.query.filter(BowlingEvent.status == "closed")
    if selected_year:
        query = query.filter(db.extract("year", BowlingEvent.event_date) == selected_year)
    return query


def report_member_name(member):
    return member.nickname or member.first_name or member.display_name()


def build_count_stat_rows(selected_year, penalty_key):
    query = (
        db.session.query(
            Member.id,
            Member.first_name,
            Member.last_name,
            Member.nickname,
            db.func.coalesce(db.func.sum(ParticipantPenalty.quantity), 0).label("total"),
        )
        .join(EventParticipant, EventParticipant.member_id == Member.id)
        .join(BowlingEvent, BowlingEvent.id == EventParticipant.event_id)
        .join(ParticipantPenalty, ParticipantPenalty.participant_id == EventParticipant.id)
        .join(PenaltyType, PenaltyType.id == ParticipantPenalty.penalty_type_id)
        .filter(BowlingEvent.status == "closed")
        .filter(PenaltyType.key == penalty_key)
    )

    if selected_year:
        query = query.filter(db.extract("year", BowlingEvent.event_date) == selected_year)

    results = (
        query.group_by(Member.id)
        .order_by(db.desc("total"), Member.first_name, Member.last_name)
        .all()
    )

    return [
        {
            "member_id": row.id,
            "name": row.nickname or row.first_name or f"{row.first_name} {row.last_name}",
            "total": int(row.total or 0),
        }
        for row in results
        if int(row.total or 0) > 0
    ]


def build_absence_stat_rows(selected_year):
    query = (
        db.session.query(
            Member.id,
            Member.first_name,
            Member.last_name,
            Member.nickname,
            db.func.sum(db.case((EventParticipant.status == "excused", 1), else_=0)).label("excused"),
            db.func.sum(db.case((EventParticipant.status == "unexcused", 1), else_=0)).label("unexcused"),
        )
        .join(EventParticipant, EventParticipant.member_id == Member.id)
        .join(BowlingEvent, BowlingEvent.id == EventParticipant.event_id)
        .filter(BowlingEvent.status == "closed")
    )

    if selected_year:
        query = query.filter(db.extract("year", BowlingEvent.event_date) == selected_year)

    results = query.group_by(Member.id).all()
    rows = []

    for row in results:
        excused = int(row.excused or 0)
        unexcused = int(row.unexcused or 0)
        total = excused + unexcused
        if total <= 0:
            continue
        rows.append({
            "member_id": row.id,
            "name": row.nickname or row.first_name or f"{row.first_name} {row.last_name}",
            "excused": excused,
            "unexcused": unexcused,
            "total": total,
        })

    rows.sort(key=lambda item: (-item["total"], item["name"].casefold()))
    return rows


def build_penalty_money_rows(selected_year):
    query = (
        db.session.query(
            Member.id,
            Member.first_name,
            Member.last_name,
            Member.nickname,
            db.func.coalesce(db.func.sum(MemberPenaltyTransaction.amount_cents), 0).label("total"),
        )
        .join(MemberPenaltyTransaction, MemberPenaltyTransaction.member_id == Member.id)
        .filter(MemberPenaltyTransaction.category == "event_penalty")
    )

    if selected_year:
        query = query.filter(db.extract("year", MemberPenaltyTransaction.booking_date) == selected_year)

    results = query.group_by(Member.id).order_by(db.desc("total"), Member.first_name, Member.last_name).all()
    return [
        {
            "name": row.nickname or row.first_name or f"{row.first_name} {row.last_name}",
            "total_cents": int(row.total or 0),
            "total_euro": cents_to_euro(row.total or 0),
        }
        for row in results
        if int(row.total or 0) > 0
    ]


def member_payment_summary(selected_year=None):
    """Summiert echte Einzahlungen je Mitglied für Admin/Kassierer.

    Gezählt werden Barzahlungen am Kegelabend und spätere Überweisungen.
    Negative Beträge in MemberPenaltyTransaction sind Zahlungen/Guthabenabbau.
    """
    query = (
        db.session.query(
            Member.id,
            Member.first_name,
            Member.last_name,
            Member.nickname,
            db.func.sum(MemberPenaltyTransaction.amount_cents).label("total_cents"),
        )
        .join(MemberPenaltyTransaction, MemberPenaltyTransaction.member_id == Member.id)
        .filter(MemberPenaltyTransaction.category.in_(["cash_payment", "bank_transfer"]))
    )

    if selected_year:
        query = query.filter(db.extract("year", MemberPenaltyTransaction.booking_date) == selected_year)

    rows = (
        query.group_by(Member.id)
        .order_by(Member.first_name.asc(), Member.last_name.asc())
        .all()
    )

    result = []
    total_paid = 0
    for row in rows:
        paid_cents = abs(row.total_cents or 0)
        total_paid += paid_cents
        name = row.nickname or f"{row.first_name} {row.last_name}"
        result.append({
            "name": name,
            "paid_cents": paid_cents,
            "paid_euro": cents_to_euro(paid_cents),
        })

    average_cents = int(round(total_paid / len(result))) if result else 0

    return result, cents_to_euro(total_paid), cents_to_euro(average_cents)


def event_year_filter(query, selected_year):
    if selected_year:
        return query.filter(db.extract("year", BowlingEvent.event_date) == selected_year)
    return query


def build_player_overview_rows(selected_year=None):
    members = Member.query.order_by(Member.first_name.asc(), Member.last_name.asc()).all()
    rows = []

    for member in members:
        participant_query = (
            EventParticipant.query
            .join(BowlingEvent, BowlingEvent.id == EventParticipant.event_id)
            .filter(EventParticipant.member_id == member.id)
            .filter(BowlingEvent.status == "closed")
        )
        participant_query = event_year_filter(participant_query, selected_year)
        participants = participant_query.all()

        attended = sum(1 for p in participants if p.status == "present")
        excused = sum(1 for p in participants if p.status == "excused")
        unexcused = sum(1 for p in participants if p.status == "unexcused")

        penalty_query = (
            MemberPenaltyTransaction.query
            .filter(MemberPenaltyTransaction.member_id == member.id)
            .filter(MemberPenaltyTransaction.category == "event_penalty")
        )
        if selected_year:
            penalty_query = penalty_query.filter(db.extract("year", MemberPenaltyTransaction.booking_date) == selected_year)
        penalty_transactions = penalty_query.all()
        penalty_total_cents = sum(t.amount_cents or 0 for t in penalty_transactions)

        payment_query = (
            MemberPenaltyTransaction.query
            .filter(MemberPenaltyTransaction.member_id == member.id)
            .filter(MemberPenaltyTransaction.category.in_(["cash_payment", "bank_transfer"]))
        )
        if selected_year:
            payment_query = payment_query.filter(db.extract("year", MemberPenaltyTransaction.booking_date) == selected_year)
        paid_cents = abs(sum(t.amount_cents or 0 for t in payment_query.all()))

        highest_single = 0
        for participant in participants:
            event_amount = sum((tx.amount_cents or 0) for tx in penalty_transactions if tx.participant_id == participant.id)
            highest_single = max(highest_single, event_amount)

        current_balance = member_penalty_balance(member.id)
        open_cents = current_balance if current_balance > 0 else 0
        credit_cents = abs(current_balance) if current_balance < 0 else 0

        rows.append({
            "name": report_member_name(member),
            "attended": attended,
            "excused": excused,
            "unexcused": unexcused,
            "absences": excused + unexcused,
            "penalty_total_cents": penalty_total_cents,
            "penalty_total_euro": cents_to_euro(penalty_total_cents),
            "highest_single_cents": highest_single,
            "highest_single_euro": cents_to_euro(highest_single),
            "paid_cents": paid_cents,
            "paid_euro": cents_to_euro(paid_cents),
            "open_cents": open_cents,
            "open_euro": cents_to_euro(open_cents),
            "credit_cents": credit_cents,
            "credit_euro": cents_to_euro(credit_cents),
        })

    return rows


def build_event_overview_rows(selected_year=None):
    events = report_event_query(selected_year).order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc()).all()
    rows = []
    for event in events:
        participants = EventParticipant.query.filter_by(event_id=event.id).all()
        present_count = sum(1 for p in participants if p.status in ["present", "guest"])
        absence_count = sum(1 for p in participants if p.status in ["excused", "unexcused"])
        member_count = sum(1 for p in participants if p.member_id is not None)
        guest_count = sum(1 for p in participants if p.status == "guest" or p.member_id is None)
        penalty_sum = sum(
            tx.amount_cents or 0
            for tx in MemberPenaltyTransaction.query
                .filter(MemberPenaltyTransaction.event_id == event.id)
                .filter(MemberPenaltyTransaction.category == "event_penalty")
                .all()
        )
        rows.append({
            "date": event.event_date,
            "present_count": present_count,
            "absence_count": absence_count,
            "member_count": member_count,
            "guest_count": guest_count,
            "penalty_sum_cents": penalty_sum,
            "penalty_sum_euro": cents_to_euro(penalty_sum),
            "lane_cost_cents": event.lane_cost_cents or 0,
            "lane_cost_euro": cents_to_euro(event.lane_cost_cents or 0),
        })
    return rows


def build_report_totals(player_rows, event_rows, selected_year=None):
    total_penalties = sum(row["penalty_total_cents"] for row in player_rows)
    total_paid = sum(row["paid_cents"] for row in player_rows)
    total_open = sum(row["open_cents"] for row in player_rows)
    total_credit = sum(row["credit_cents"] for row in player_rows)
    total_lane_costs = sum(row["lane_cost_cents"] for row in event_rows)
    average_participants = 0
    if event_rows:
        average_participants = round(sum(row["present_count"] for row in event_rows) / len(event_rows), 1)

    cashbook_query = CashbookEntry.query.filter(CashbookEntry.is_void == False)  # noqa: E712
    if selected_year:
        cashbook_query = cashbook_query.filter(db.extract("year", CashbookEntry.booking_date) == selected_year)
    entries = cashbook_query.all()
    income = sum(e.amount_cents or 0 for e in entries if e.direction == "income")
    expense = sum(e.amount_cents or 0 for e in entries if e.direction == "expense")

    return {
        "total_penalties_euro": cents_to_euro(total_penalties),
        "total_paid_euro": cents_to_euro(total_paid),
        "total_open_euro": cents_to_euro(total_open),
        "total_credit_euro": cents_to_euro(total_credit),
        "total_lane_costs_euro": cents_to_euro(total_lane_costs),
        "average_participants": str(average_participants).replace(".", ","),
        "cashbook_income_euro": cents_to_euro(income),
        "cashbook_expense_euro": cents_to_euro(expense),
    }


def top_rows(rows, key, reverse=True, limit=10):
    filtered = [row for row in rows if row.get(key, 0)]
    return sorted(filtered, key=lambda row: (row.get(key, 0), row.get("name", "")), reverse=reverse)[:limit]


def cashbook_category_options():
    return [
        "Kegeltour",
        "Weihnachtsfeier",
        "Geschenk",
        "Bahnkosten",
        "Umbuchung",
        "Mitgliedsbeitrag",
        "Zinsen",
        "Sonstige Ausgabe",
        "Sonstige Einnahme",
        "Rückerstattung",
    ]




def interest_setting_snapshot(setting):
    return {
        "Zinssatz": f"{setting.rate_percent()} % p. a.",
        "Gültig ab": setting.valid_from.isoformat() if setting.valid_from else "",
        "Auszahlungsrhythmus": setting.frequency_label(),
        "Aktiv": setting.active,
        "Notiz": setting.note or "",
    }


def interest_booking_snapshot(booking):
    return {
        "Buchungsdatum": booking.booking_date.isoformat() if booking.booking_date else "",
        "Zeitraum": f"{booking.period_start.isoformat()} bis {booking.period_end.isoformat()}",
        "Zinssatz": f"{booking.setting.rate_percent()} % p. a." if booking.setting else "-",
        "Bankbestand Grundlage": f"{cents_to_euro(booking.basis_balance_cents)} €",
        "Erwartete Zinsen": f"{cents_to_euro(booking.expected_interest_cents)} €",
        "Tatsächlich gebucht": f"{cents_to_euro(booking.actual_interest_cents)} €",
        "Notiz": booking.note or "",
    }


def parse_interest_rate_basis_points(raw):
    """Wandelt Prozent-Eingaben robust in Basispunkte um: 1,25 -> 125."""
    value = (raw or "").strip().replace("%", "").replace(" ", "")
    if not value:
        return 0
    value = value.replace(".", "").replace(",", ".")
    try:
        percent = Decimal(value)
    except Exception as exc:
        raise ValueError("Bitte einen gültigen Zinssatz eingeben, z. B. 1,25.") from exc
    return int((percent * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def interest_period_months(frequency):
    return {
        "monthly": 1,
        "quarterly": 3,
        "half_yearly": 6,
        "yearly": 12,
    }.get(frequency, 12)


def calculate_expected_interest_cents(balance_cents, annual_rate_basis_points, period_start, period_end):
    if not balance_cents or not annual_rate_basis_points or not period_start or not period_end:
        return 0
    days = max((period_end - period_start).days + 1, 0)
    if days <= 0:
        return 0
    cents = (
        Decimal(balance_cents)
        * Decimal(annual_rate_basis_points)
        / Decimal("10000")
        * Decimal(days)
        / Decimal("365")
    )
    return int(cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def default_interest_period(setting):
    today = datetime.today().date()

    first_day_current_month = today.replace(day=1)
    previous_month_end = first_day_current_month - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)

    return previous_month_start, previous_month_end


def annual_year_summary(year):
    start = datetime(year, 1, 1).date()
    end = datetime(year + 1, 1, 1).date()

    transactions = AccountTransaction.query.filter(
        AccountTransaction.booking_date >= start,
        AccountTransaction.booking_date < end,
    ).all()
    income_cents = sum(t.amount_cents for t in transactions if (t.amount_cents or 0) > 0)
    expense_cents = abs(sum(t.amount_cents for t in transactions if (t.amount_cents or 0) < 0))

    members = Member.query.all()
    open_penalties_cents = 0
    member_credits_cents = 0
    for member in members:
        balance = member_penalty_balance(member.id)
        if balance > 0:
            open_penalties_cents += balance
        elif balance < 0:
            member_credits_cents += abs(balance)

    last_audit = CashAudit.query.filter(CashAudit.audit_date <= end - timedelta(days=1)).order_by(CashAudit.audit_date.desc(), CashAudit.created_at.desc()).first()

    return {
        "cash_balance_cents": account_balance("cash"),
        "bank_balance_cents": account_balance("bank"),
        "open_penalties_cents": open_penalties_cents,
        "member_credits_cents": member_credits_cents,
        "event_count": BowlingEvent.query.filter(BowlingEvent.event_date >= start, BowlingEvent.event_date < end, BowlingEvent.status == "closed").count(),
        "cancelled_event_count": BowlingEvent.query.filter(BowlingEvent.event_date >= start, BowlingEvent.event_date < end, BowlingEvent.status == "cancelled").count(),
        "open_event_count": BowlingEvent.query.filter(BowlingEvent.event_date >= start, BowlingEvent.event_date < end, BowlingEvent.status == "open").count(),
        "income_cents": income_cents,
        "expense_cents": expense_cents,
        "last_audit": last_audit,
    }


@app.route("/annual-closings", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def annual_closings():
    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    current_year = datetime.today().year

    if request.method == "POST":
        try:
            year = int(request.form.get("year", current_year))
        except ValueError:
            flash("Bitte ein gültiges Jahr eingeben.", "danger")
            return redirect(url_for("annual_closings"))

        if AnnualClosing.query.filter_by(year=year).first():
            flash("Für dieses Jahr gibt es bereits einen Jahresabschluss. Bitte vorhandenen Abschluss prüfen, statt doppelt anzulegen.", "warning")
            return redirect(url_for("annual_closings"))

        summary = annual_year_summary(year)
        if summary["open_event_count"] > 0:
            flash("Jahresabschluss nicht gespeichert: Für dieses Jahr gibt es noch offene Kegelabende.", "danger")
            return redirect(url_for("annual_closings"))

        note = request.form.get("note", "").strip()
        closing = AnnualClosing(
            year=year,
            closing_date=datetime.today().date(),
            cash_balance_cents=summary["cash_balance_cents"],
            bank_balance_cents=summary["bank_balance_cents"],
            total_balance_cents=summary["cash_balance_cents"] + summary["bank_balance_cents"],
            open_penalties_cents=summary["open_penalties_cents"],
            member_credits_cents=summary["member_credits_cents"],
            event_count=summary["event_count"],
            cancelled_event_count=summary["cancelled_event_count"],
            open_event_count=summary["open_event_count"],
            income_cents=summary["income_cents"],
            expense_cents=summary["expense_cents"],
            last_cash_audit_id=summary["last_audit"].id if summary["last_audit"] else None,
            note=note,
            created_by_user_id=current_user.id,
        )
        db.session.add(closing)
        db.session.flush()

        details = "\n".join(f"{key}: {value}" for key, value in annual_closing_snapshot(closing).items())
        audit_log(
            "annual_closing",
            "annual_closing_created",
            f"Jahresabschluss {year} gespeichert",
            details=details,
            object_type="AnnualClosing",
            object_id=closing.id,
            new_value=details,
        )
        db.session.commit()
        flash(f"Jahresabschluss {year} wurde gespeichert.", "success")
        return redirect(url_for("annual_closings"))

    year_raw = request.args.get("year")
    try:
        selected_year = int(year_raw) if year_raw else current_year - 1
    except ValueError:
        selected_year = current_year - 1

    summary = annual_year_summary(selected_year)
    closings = AnnualClosing.query.order_by(AnnualClosing.year.desc()).all()
    years = sorted(set([current_year - i for i in range(0, 8)] + [c.year for c in closings]), reverse=True)

    return render_template(
        "annual_closings.html",
        years=years,
        selected_year=selected_year,
        summary=summary,
        closings=closings,
        existing_closing=AnnualClosing.query.filter_by(year=selected_year).first(),
        cash_balance=cents_to_euro(summary["cash_balance_cents"]),
        bank_balance=cents_to_euro(summary["bank_balance_cents"]),
        total_balance=cents_to_euro(summary["cash_balance_cents"] + summary["bank_balance_cents"]),
        open_penalties=cents_to_euro(summary["open_penalties_cents"]),
        member_credits=cents_to_euro(summary["member_credits_cents"]),
        income=cents_to_euro(summary["income_cents"]),
        expense=cents_to_euro(summary["expense_cents"]),
    )


@app.route("/annual-closings/<int:closing_id>/confirm", methods=["POST"])
@login_required
@role_required("auditor")
def confirm_annual_closing(closing_id):
    closing = AnnualClosing.query.get_or_404(closing_id)
    if closing.confirmed_at:
        flash("Dieser Jahresabschluss wurde bereits bestätigt.", "warning")
        return redirect(url_for("annual_closings", year=closing.year))

    password = request.form.get("confirm_password", "")
    auditor_note = (request.form.get("auditor_note") or "").strip()
    if not check_password_hash(current_user.password_hash, password):
        flash("Das Passwort ist nicht korrekt. Der Jahresabschluss wurde nicht bestätigt.", "danger")
        return redirect(url_for("annual_closings", year=closing.year))

    closing.confirmed_by_user_id = current_user.id
    closing.confirmed_at = datetime.utcnow()
    closing.auditor_note = auditor_note

    details = "\n".join([
        f"Jahr: {closing.year}",
        f"Prüfer/-in: {current_user.username}",
        f"Barkasse: {closing.cash_balance_euro()} €",
        f"Bank: {closing.bank_balance_euro()} €",
        f"Gesamtbestand: {closing.total_balance_euro()} €",
        f"Prüfernotiz: {auditor_note or '-'}",
    ])
    audit_log(
        "annual_closing",
        "annual_closing_confirmed",
        f"Jahresabschluss {closing.year} bestätigt",
        details=details,
        object_type="AnnualClosing",
        object_id=closing.id,
        new_value="\n".join(f"{key}: {value}" for key, value in annual_closing_snapshot(closing).items()),
    )
    db.session.commit()
    flash("Jahresabschluss wurde bestätigt und im Revisionsprotokoll dokumentiert.", "success")
    return redirect(url_for("annual_closings", year=closing.year))

@app.route("/cash-audits", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def cash_audits():
    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    expected_cash_cents = account_balance("cash")
    expected_bank_cents = account_balance("bank")

    if request.method == "POST":
        audit_date_raw = request.form.get("audit_date") or datetime.today().date().isoformat()
        try:
            audit_date = datetime.strptime(audit_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Bitte ein gültiges Prüfdatum eingeben.", "danger")
            return redirect(url_for("cash_audits"))

        try:
            counted_cash_cents = form_euro_to_cents("counted_cash", "Barkasse gezählt")
            statement_bank_cents = form_euro_to_cents("statement_bank", "Bank laut Auszug")
        except ValueError:
            return redirect(url_for("cash_audits"))

        note = request.form.get("note", "").strip()
        audit = CashAudit(
            audit_date=audit_date,
            expected_cash_cents=expected_cash_cents,
            counted_cash_cents=counted_cash_cents,
            difference_cash_cents=counted_cash_cents - expected_cash_cents,
            expected_bank_cents=expected_bank_cents,
            statement_bank_cents=statement_bank_cents,
            difference_bank_cents=statement_bank_cents - expected_bank_cents,
            note=note,
            created_by_user_id=current_user.id,
        )
        db.session.add(audit)
        db.session.flush()

        details = "\n".join([
            f"Barkasse: System {cents_to_euro(expected_cash_cents)} € → gezählt {cents_to_euro(counted_cash_cents)} €",
            f"Barkasse Differenz: {cents_to_euro(audit.difference_cash_cents)} €",
            f"Bank: System {cents_to_euro(expected_bank_cents)} € → Auszug {cents_to_euro(statement_bank_cents)} €",
            f"Bank Differenz: {cents_to_euro(audit.difference_bank_cents)} €",
            f"Notiz: {note or '-'}",
        ])
        audit_log(
            "finance",
            "cash_audit_created",
            f"Kassenprüfung vom {audit_date.strftime('%d.%m.%Y')} gespeichert",
            details=details,
            object_type="CashAudit",
            object_id=audit.id,
            new_value="\n".join(f"{key}: {value}" for key, value in cash_audit_snapshot(audit).items()),
        )
        db.session.commit()
        flash("Kassenprüfung wurde gespeichert.", "success")
        return redirect(url_for("cash_audits"))

    audits = CashAudit.query.order_by(CashAudit.audit_date.desc(), CashAudit.created_at.desc()).all()
    return render_template(
        "cash_audits.html",
        audits=audits,
        today=datetime.today().date().isoformat(),
        expected_cash=cents_to_euro(expected_cash_cents),
        expected_bank=cents_to_euro(expected_bank_cents),
    )


@app.route("/cash-audits/<int:audit_id>/confirm", methods=["POST"])
@login_required
@role_required("auditor")
def confirm_cash_audit(audit_id):
    audit = CashAudit.query.get_or_404(audit_id)
    if audit.confirmed_at:
        flash("Diese Kassenprüfung wurde bereits bestätigt.", "warning")
        return redirect(url_for("cash_audits"))

    password = request.form.get("confirm_password", "")
    auditor_note = (request.form.get("auditor_note") or "").strip()
    if not check_password_hash(current_user.password_hash, password):
        flash("Das Passwort ist nicht korrekt. Die Prüfung wurde nicht bestätigt.", "danger")
        return redirect(url_for("cash_audits"))

    audit.confirmed_by_user_id = current_user.id
    audit.confirmed_at = datetime.utcnow()
    audit.auditor_note = auditor_note

    details = "\n".join([
        f"Prüfer/-in: {current_user.username}",
        f"Prüfdatum: {audit.audit_date.strftime('%d.%m.%Y') if audit.audit_date else '-'}",
        f"Barkasse Differenz: {cents_to_euro(audit.difference_cash_cents)} €",
        f"Bank Differenz: {cents_to_euro(audit.difference_bank_cents)} €",
        f"Prüfernotiz: {auditor_note or '-'}",
    ])
    audit_log(
        "finance",
        "cash_audit_confirmed",
        f"Kassenprüfung vom {audit.audit_date.strftime('%d.%m.%Y') if audit.audit_date else audit.id} bestätigt",
        details=details,
        object_type="CashAudit",
        object_id=audit.id,
        new_value="\n".join(f"{key}: {value}" for key, value in cash_audit_snapshot(audit).items()),
    )
    db.session.commit()
    flash("Kassenprüfung wurde bestätigt und im Revisionsprotokoll dokumentiert.", "success")
    return redirect(url_for("cash_audits"))

@app.route("/api/interest/calculate", methods=["POST"])
@login_required
@role_required("admin", "cashier")
def api_interest_calculate():
    data = request.get_json(silent=True) or {}
    gross_interest_cents = euro_to_cents(data.get("gross_interest", "0"))

    taxes = calculate_interest_taxes(gross_interest_cents)

    return jsonify({
        "capital_tax": cents_to_euro(taxes["capital_tax"]),
        "solidarity_tax": cents_to_euro(taxes["solidarity_tax"]),
        "church_tax": cents_to_euro(taxes["church_tax"]),
        "total_tax": cents_to_euro(
            taxes["capital_tax"]
            + taxes["solidarity_tax"]
            + taxes["church_tax"]
        ),
        "net_interest": cents_to_euro(max(0, taxes["net"])),
    })

@app.route("/interest", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def interest_module():
    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    active_setting = InterestSetting.query.filter_by(active=True).order_by(InterestSetting.valid_from.desc(), InterestSetting.created_at.desc()).first()

    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "setting":
            valid_from_raw = request.form.get("valid_from") or datetime.today().date().isoformat()
            try:
                valid_from = datetime.strptime(valid_from_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Bitte ein gültiges Datum für 'gültig ab' eingeben.", "danger")
                return redirect(url_for("interest_module"))

            frequency = request.form.get("payout_frequency", "yearly")
            if frequency not in ["monthly", "quarterly", "half_yearly", "yearly"]:
                frequency = "yearly"

            try:
                basis_points = parse_interest_rate_basis_points(request.form.get("annual_rate"))
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("interest_module"))

            note = request.form.get("note", "").strip()
            for old in InterestSetting.query.filter_by(active=True).all():
                old.active = False

            setting = InterestSetting(
                annual_rate_basis_points=basis_points,
                valid_from=valid_from,
                payout_frequency=frequency,
                active=True,
                note=note,
                created_by_user_id=current_user.id,
            )
            db.session.add(setting)
            db.session.flush()
            details = "\n".join(f"{key}: {value}" for key, value in interest_setting_snapshot(setting).items())
            audit_log(
                "interest",
                "interest_setting_created",
                "Zinseinstellung gespeichert",
                details=details,
                object_type="InterestSetting",
                object_id=setting.id,
                new_value=details,
            )
            db.session.commit()
            flash("Zinseinstellung wurde gespeichert.", "success")
            return redirect(url_for("interest_module"))

        if form_type == "booking":
            if not active_setting:
                flash("Bitte zuerst eine aktive Zinseinstellung speichern.", "danger")
                return redirect(url_for("interest_module"))

            try:
                booking_date = datetime.strptime(request.form.get("booking_date") or datetime.today().date().isoformat(), "%Y-%m-%d").date()
                period_start = datetime.strptime(request.form.get("period_start"), "%Y-%m-%d").date()
                period_end = datetime.strptime(request.form.get("period_end"), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                flash("Bitte gültige Datumswerte für Buchung und Zeitraum eingeben.", "danger")
                return redirect(url_for("interest_module"))

            if period_end < period_start:
                flash("Der Zeitraum ist ungültig: Bis-Datum liegt vor dem Von-Datum.", "danger")
                return redirect(url_for("interest_module"))

            existing_interest_booking = (
                InterestBooking.query
                .filter(InterestBooking.period_start == period_start)
                .filter(InterestBooking.period_end == period_end)
                .filter(InterestBooking.is_cancelled == False)
                .first()
            )

            if existing_interest_booking:
                flash(
                    "Für diesen Zinszeitraum wurde bereits eine aktive Zinsgutschrift gebucht. Eine Doppelbuchung ist nicht erlaubt.",
                    "warning"
                )
                return redirect(url_for("interest_module"))

            basis_balance_cents = account_balance("bank")
            expected_cents = calculate_expected_interest_cents(
                basis_balance_cents,
                active_setting.annual_rate_basis_points,
                period_start,
                period_end,
            )

            try:
                actual_cents = form_euro_to_cents("actual_interest", "Tatsächliche Zinsen")
            except ValueError:
                return redirect(url_for("interest_module"))

            calculated_taxes = calculate_interest_taxes(actual_cents)

            try:
                capital_gains_tax_cents = form_euro_to_cents("capital_gains_tax", "Kapitalertragsteuer")
                solidarity_tax_cents = form_euro_to_cents("solidarity_tax", "Solidaritätszuschlag")
                church_tax_cents = form_euro_to_cents("church_tax", "Kirchensteuer")
            except ValueError:
                capital_gains_tax_cents = calculated_taxes["capital_tax"]
                solidarity_tax_cents = calculated_taxes["solidarity_tax"]
                church_tax_cents = calculated_taxes["church_tax"]

            tax_cents = capital_gains_tax_cents + solidarity_tax_cents + church_tax_cents
            net_cents = actual_cents - tax_cents

            if actual_cents <= 0:
                flash("Der Zinsbetrag muss größer als 0,00 € sein.", "danger")
                return redirect(url_for("interest_module"))
            if net_cents < 0:
                flash("Die Abzüge dürfen nicht höher als die Brutto-Zinsen sein.", "danger")
                return redirect(url_for("interest_module"))

            note = request.form.get("booking_note", "").strip()
            cashbook_entry = CashbookEntry(
                booking_date=booking_date,
                direction="income",
                account="bank",
                amount_cents=net_cents,
                category="Zinsen",
                person="Bank",
                reason=f"Netto-Zinsgutschrift {period_start.strftime('%d.%m.%Y')} - {period_end.strftime('%d.%m.%Y')}",
                note=note,
                created_by_user_id=current_user.id,
            )
            db.session.add(cashbook_entry)
            db.session.flush()

            db.session.add(AccountTransaction(
                account="bank",
                category="interest",
                amount_cents=net_cents,
                booking_date=booking_date,
                description=f"Netto-Zinsgutschrift {period_start.isoformat()} bis {period_end.isoformat()}",
            ))

            booking = InterestBooking(
                booking_date=booking_date,
                period_start=period_start,
                period_end=period_end,
                setting_id=active_setting.id,
                basis_balance_cents=basis_balance_cents,
                expected_interest_cents=expected_cents,
                actual_interest_cents=actual_cents,
                capital_gains_tax_cents=capital_gains_tax_cents,
                solidarity_tax_cents=solidarity_tax_cents,
                church_tax_cents=church_tax_cents,
                tax_cents=tax_cents,
                net_interest_cents=net_cents,
                cashbook_entry_id=cashbook_entry.id,
                note=note,
                created_by_user_id=current_user.id,
            )
            db.session.add(booking)
            db.session.flush()

            details = "\n".join(f"{key}: {value}" for key, value in interest_booking_snapshot(booking).items())
            audit_log(
                "interest",
                "interest_booking_created",
                f"Zinsgutschrift {cents_to_euro(actual_cents)} € gebucht",
                details=details,
                object_type="InterestBooking",
                object_id=booking.id,
                new_value=details,
            )
            db.session.commit()
            flash("Zinsgutschrift wurde gebucht und im Kassenbuch erfasst.", "success")
            return redirect(url_for("interest_module"))

    if active_setting:
        default_start, default_end = default_interest_period(active_setting)
    else:
        today = datetime.today().date()
        default_start, default_end = datetime(today.year, 1, 1).date(), today

    bank_balance_cents = account_balance("bank")
    expected_cents = calculate_expected_interest_cents(
        bank_balance_cents,
        active_setting.annual_rate_basis_points if active_setting else 0,
        default_start,
        default_end,
    )
    settings = InterestSetting.query.order_by(InterestSetting.valid_from.desc(), InterestSetting.created_at.desc()).all()
    bookings = InterestBooking.query.order_by(InterestBooking.booking_date.desc(), InterestBooking.created_at.desc()).all()

    suggested_taxes = calculate_interest_taxes(expected_cents)

    finance_settings = get_finance_settings()

    return render_template(
        "interest.html",
        active_setting=active_setting,
        settings=settings,
        bookings=bookings,
        today=datetime.today().date().isoformat(),
        default_start=default_start.isoformat(),
        default_end=default_end.isoformat(),
        bank_balance=cents_to_euro(bank_balance_cents),
        expected_interest=cents_to_euro(expected_cents),
        suggested_capital_gains_tax=cents_to_euro(suggested_taxes["capital_tax"]),
        suggested_solidarity_tax=cents_to_euro(suggested_taxes["solidarity_tax"]),
        suggested_church_tax=cents_to_euro(suggested_taxes["church_tax"]),
        finance_settings=finance_settings,
    )

@app.route("/interest/<int:booking_id>/cancel", methods=["POST"])
@login_required
@role_required("admin", "cashier")
def interest_booking_cancel(booking_id):
    booking = InterestBooking.query.get_or_404(booking_id)

    if booking.is_cancelled:
        flash("Diese Zinsbuchung wurde bereits storniert.", "warning")
        return redirect(url_for("interest_module"))

    reason = request.form.get("cancel_reason", "").strip()
    if not reason:
        reason = "Stornierung der Zinsbuchung"

    reversal_entry = CashbookEntry(
        booking_date=datetime.today().date(),
        direction="expense",
        account="bank",
        amount_cents=booking.net_interest_cents,
        category="Zinsen Storno",
        person="Bank",
        reason=f"Storno Zinsgutschrift {booking.period_start.strftime('%d.%m.%Y')} - {booking.period_end.strftime('%d.%m.%Y')}",
        note=reason,
        created_by_user_id=current_user.id,
    )
    db.session.add(reversal_entry)
    db.session.flush()

    db.session.add(AccountTransaction(
        account="bank",
        category="interest_cancel",
        amount_cents=-booking.net_interest_cents,
        booking_date=datetime.today().date(),
        description=f"Storno Zinsgutschrift #{booking.id}",
    ))

    booking.is_cancelled = True
    booking.cancelled_at = datetime.utcnow()
    booking.cancelled_by_user_id = current_user.id
    booking.cancel_reason = reason
    booking.reversal_cashbook_entry_id = reversal_entry.id

    audit_log(
        "interest",
        "interest_booking_cancelled",
        f"Zinsgutschrift {booking.actual_interest_euro()} € storniert",
        details=(
            f"Zeitraum: {booking.period_start} bis {booking.period_end}\n"
            f"Brutto: {booking.actual_interest_euro()} €\n"
            f"Netto: {booking.net_interest_euro()} €\n"
            f"Grund: {reason}"
        ),
        object_type="InterestBooking",
        object_id=booking.id,
        old_value="gebucht",
        new_value="storniert",
    )

    db.session.commit()
    flash("Zinsbuchung wurde storniert und als Gegenbuchung im Kassenbuch erfasst.", "success")
    return redirect(url_for("interest_module"))

@app.route("/cashbook", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def cashbook():
    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    if request.method == "POST":
        booking_date_raw = request.form.get("booking_date") or datetime.today().date().isoformat()
        try:
            booking_date = datetime.strptime(booking_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Bitte ein gültiges Datum eingeben.", "danger")
            return redirect(url_for("cashbook"))

        direction = request.form.get("direction", "expense")
        account = request.form.get("account", "cash")
        transfer_to_account = request.form.get("transfer_to_account", "bank")
        category = request.form.get("category", "Sonstige Ausgabe").strip()
        person = request.form.get("person", "").strip()
        reason = request.form.get("reason", "").strip()
        note = request.form.get("note", "").strip()

        if direction not in ["income", "expense", "transfer"]:
            flash("Bitte Einnahme, Ausgabe oder Umbuchung auswählen.", "danger")
            return redirect(url_for("cashbook"))

        if account not in ["cash", "bank"]:
            flash("Bitte Barkasse oder Bank auswählen.", "danger")
            return redirect(url_for("cashbook"))

        if direction == "transfer":
            if transfer_to_account not in ["cash", "bank"]:
                flash("Bitte ein gültiges Zielkonto auswählen.", "danger")
                return redirect(url_for("cashbook"))
            if transfer_to_account == account:
                flash("Bei einer Umbuchung müssen Quellkonto und Zielkonto unterschiedlich sein.", "danger")
                return redirect(url_for("cashbook"))
            category = "Umbuchung"
            person = transfer_to_account

        if not reason:
            flash("Bitte einen Grund eintragen.", "danger")
            return redirect(url_for("cashbook"))

        try:
            amount_cents = form_euro_to_cents("amount", "Betrag")
        except ValueError:
            return redirect(url_for("cashbook"))

        if amount_cents <= 0:
            flash("Der Betrag muss größer als 0,00 € sein.", "danger")
            return redirect(url_for("cashbook"))

        entry = CashbookEntry(
            booking_date=booking_date,
            direction=direction,
            account=account,
            amount_cents=amount_cents,
            category=category,
            person=person,
            reason=reason,
            note=note,
            created_by_user_id=current_user.id,
        )
        db.session.add(entry)

        if direction == "transfer":
            db.session.add(AccountTransaction(
                account=account,
                category="cashbook_transfer",
                amount_cents=-amount_cents,
                booking_date=booking_date,
                description=f"Umbuchung: {account} → {transfer_to_account} - {reason}",
            ))
            db.session.add(AccountTransaction(
                account=transfer_to_account,
                category="cashbook_transfer",
                amount_cents=amount_cents,
                booking_date=booking_date,
                description=f"Umbuchung: {account} → {transfer_to_account} - {reason}",
            ))
        else:
            signed_amount = amount_cents if direction == "income" else -amount_cents
            db.session.add(AccountTransaction(
                account=account,
                category="cashbook_manual",
                amount_cents=signed_amount,
                booking_date=booking_date,
                description=f"Kassenbuch: {category} - {reason}" + (f" ({person})" if person else ""),
            ))

        audit_log(
            "finance",
            "cashbook_entry_created",
            f"Kassenbuch-Eintrag gespeichert: {entry.direction_label()} {cents_to_euro(amount_cents)} €",
            details=f"Kategorie: {category}; Konto: {entry.account_label()}; Empfänger/Einzahler: {person or '-'}; Grund: {reason}",
            object_type="CashbookEntry",
            object_id=entry.id,
        )
        db.session.commit()
        flash("Kassenbuch-Eintrag gespeichert.", "success")
        return redirect(url_for("cashbook"))

    filters = {
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to": (request.args.get("date_to") or "").strip(),
        "direction": (request.args.get("direction") or "all").strip(),
        "account": (request.args.get("account") or "all").strip(),
        "amount": (request.args.get("amount") or "").strip(),
        "person": (request.args.get("person") or "").strip(),
        "reason": (request.args.get("reason") or "").strip(),
        "created_by": (request.args.get("created_by") or "").strip(),
    }

    query = CashbookEntry.query

    if filters["date_from"]:
        try:
            query = query.filter(CashbookEntry.booking_date >= datetime.strptime(filters["date_from"], "%Y-%m-%d").date())
        except ValueError:
            flash("Filter ignoriert: Datum von ist ungültig.", "warning")
            filters["date_from"] = ""

    if filters["date_to"]:
        try:
            query = query.filter(CashbookEntry.booking_date <= datetime.strptime(filters["date_to"], "%Y-%m-%d").date())
        except ValueError:
            flash("Filter ignoriert: Datum bis ist ungültig.", "warning")
            filters["date_to"] = ""

    if filters["direction"] in ["income", "expense", "transfer"]:
        query = query.filter(CashbookEntry.direction == filters["direction"])

    if filters["account"] in ["cash", "bank"]:
        query = query.filter(CashbookEntry.account == filters["account"])

    if filters["amount"]:
        try:
            query = query.filter(CashbookEntry.amount_cents == euro_to_cents(filters["amount"]))
        except ValueError:
            flash("Filter ignoriert: Betrag ist ungültig.", "warning")
            filters["amount"] = ""

    if filters["person"]:
        like = f"%{filters['person']}%"
        query = query.filter(CashbookEntry.person.ilike(like))

    if filters["reason"]:
        like = f"%{filters['reason']}%"
        query = query.filter(or_(CashbookEntry.reason.ilike(like), CashbookEntry.note.ilike(like)))

    if filters["created_by"]:
        like = f"%{filters['created_by']}%"
        query = query.join(User, CashbookEntry.created_by_user_id == User.id, isouter=True).filter(User.username.ilike(like))

    entries = query.order_by(
        CashbookEntry.booking_date.desc(),
        CashbookEntry.created_at.desc(),
    ).all()

    return render_template(
        "cashbook.html",
        entries=entries,
        categories=cashbook_category_options(),
        filters=filters,
        today=datetime.today().date().isoformat(),
        cash_balance=cents_to_euro(account_balance("cash")),
        bank_balance=cents_to_euro(account_balance("bank")),
        now=datetime.now(),
    )


@app.route("/cashbook/<int:entry_id>/void", methods=["POST"])
@login_required
@role_required("admin", "cashier")
def cashbook_void(entry_id):
    entry = CashbookEntry.query.get_or_404(entry_id)

    if entry.is_void:
        flash("Dieser Eintrag ist bereits storniert.", "warning")
        return redirect(url_for("cashbook"))

    reason = request.form.get("void_reason", "").strip()
    if not reason:
        flash("Bitte einen Storno-Grund eintragen.", "danger")
        return redirect(url_for("cashbook"))

    entry.is_void = True
    entry.voided_at = datetime.utcnow()
    entry.voided_by_user_id = current_user.id
    entry.void_reason = reason

    if entry.direction == "transfer":
        source_account = entry.account
        target_account = entry.person
        if target_account not in ["cash", "bank"]:
            flash("Diese Umbuchung kann nicht automatisch storniert werden, weil das Zielkonto nicht eindeutig ist.", "danger")
            return redirect(url_for("cashbook"))

        db.session.add(AccountTransaction(
            account=source_account,
            category="cashbook_void",
            amount_cents=entry.amount_cents,
            booking_date=datetime.today().date(),
            description=f"Storno Umbuchung #{entry.id}: {reason}",
        ))
        db.session.add(AccountTransaction(
            account=target_account,
            category="cashbook_void",
            amount_cents=-entry.amount_cents,
            booking_date=datetime.today().date(),
            description=f"Storno Umbuchung #{entry.id}: {reason}",
        ))
    else:
        db.session.add(AccountTransaction(
            account=entry.account,
            category="cashbook_void",
            amount_cents=-entry.signed_amount_cents(),
            booking_date=datetime.today().date(),
            description=f"Storno Kassenbuch #{entry.id}: {reason}",
        ))

    audit_log(
        "finance",
        "cashbook_entry_voided",
        f"Kassenbuch-Eintrag #{entry.id} storniert",
        details=reason,
        object_type="CashbookEntry",
        object_id=entry.id,
    )
    db.session.commit()
    flash("Kassenbuch-Eintrag wurde storniert.", "success")
    return redirect(url_for("cashbook"))


def parse_month_param(value):
    if not value:
        today = datetime.today().date()
        previous_month = today.replace(day=1) - timedelta(days=1)
        return previous_month.year, previous_month.month

    try:
        parsed = datetime.strptime(value, "%Y-%m").date()
        return parsed.year, parsed.month
    except ValueError:
        today = datetime.today().date()
        previous_month = today.replace(day=1) - timedelta(days=1)
        return previous_month.year, previous_month.month


def month_label(year, month):
    return f"{month:02d}.{year}"


def first_day_of_month(year, month):
    return datetime(year, month, 1).date()


def shift_weekend_to_monday(value_date):
    """Verschiebt Samstag/Sonntag auf den folgenden Montag.

    Feiertage bleiben bewusst außen vor, weil das Bundesland sonst zusätzlich gepflegt werden müsste.
    """
    while value_date.weekday() >= 5:
        value_date += timedelta(days=1)
    return value_date


def proposed_member_paid_date(member_id, year, month):
    """Schlägt den gespeicherten Dauerauftragstag des Mitglieds vor.

    Wichtig: Der gespeicherte Basistag bleibt z. B. der 7. eines Monats.
    Fällt dieser Tag auf Samstag/Sonntag, wird nur der konkrete Vorschlag
    auf den nächsten Montag verschoben. Dieser Montag wird nicht automatisch
    zum neuen Basistag.
    """
    member = Member.query.get(member_id)
    day = getattr(member, "monthly_value_day", None) if member else None

    if not day:
        previous_payment = (
            MonthlyContributionPayment.query
            .join(MonthlyContributionBatch)
            .filter(MonthlyContributionPayment.member_id == member_id)
            .filter(MonthlyContributionPayment.paid_date.isnot(None))
            .filter(MonthlyContributionPayment.paid_cents > 0)
            .filter(
                db.or_(
                    MonthlyContributionBatch.year < year,
                    db.and_(
                        MonthlyContributionBatch.year == year,
                        MonthlyContributionBatch.month < month,
                    ),
                )
            )
            .order_by(MonthlyContributionBatch.year.desc(), MonthlyContributionBatch.month.desc())
            .first()
        )
        if previous_payment and previous_payment.paid_date:
            day = previous_payment.paid_date.day

    if not day:
        return None

    day = min(max(int(day), 1), last_day_of_month(year, month))
    return shift_weekend_to_monday(datetime(year, month, day).date())


def update_member_monthly_value_day(member, selected_date, year, month):
    """Aktualisiert den Dauerauftragstag nur bei echter Änderung.

    Wenn der gespeicherte Basistag wegen Wochenende auf Montag vorgeschlagen
    wurde und genau dieser Vorschlag übernommen wird, bleibt der Basistag
    unverändert. Wählt der Kassierer bewusst einen anderen Tag, wird dieser
    Tag als neuer Basistag gespeichert.
    """
    if not member or not selected_date:
        return

    current_day = getattr(member, "monthly_value_day", None)
    if not current_day:
        member.monthly_value_day = selected_date.day
        return

    base_day = min(max(int(current_day), 1), last_day_of_month(year, month))
    expected_from_base = shift_weekend_to_monday(datetime(year, month, base_day).date())
    if selected_date != expected_from_base:
        member.monthly_value_day = selected_date.day


def monthly_contribution_rows(year, month, batch=None):
    target_date = first_day_of_month(year, month)
    default_expected = current_rate_cents("monthly_fee", target_date)

    members = (
        Member.query
        .filter_by(active=True)
        .order_by(Member.first_name.asc(), Member.last_name.asc())
        .all()
    )

    existing = {}
    if batch:
        existing = {payment.member_id: payment for payment in batch.payments}

    rows = []
    for member in members:
        payment = existing.get(member.id)
        expected = payment.expected_cents if payment else default_expected
        paid = payment.paid_cents if payment else 0
        note = payment.note if payment else ""
        paid_date = getattr(payment, "paid_date", None) if payment else None
        if paid_date:
            paid_date_value = paid_date.isoformat()
        else:
            suggested_date = proposed_member_paid_date(member.id, year, month)
            paid_date_value = suggested_date.isoformat() if suggested_date else ""

        if paid <= 0:
            status = "offen"
        elif paid < expected:
            status = "teilweise bezahlt"
        elif paid > expected:
            status = "überzahlt"
        else:
            status = "bezahlt"

        rows.append({
            "member": member,
            "payment": payment,
            "expected_cents": expected,
            "expected_euro": cents_to_euro(expected),
            "paid_cents": paid,
            "paid_euro": cents_to_euro(paid),
            "difference_euro": cents_to_euro(paid - expected),
            "status": status,
            "note": note,
            "paid_date": paid_date_value,
            "monthly_value_day": member.monthly_value_day,
        })

    return rows

@app.route("/settings/finance", methods=["GET", "POST"])
@login_required
@role_required("admin")
def finance_settings():
    defaults = {
        "tax_capital_enabled": "1",
        "tax_capital_rate": "25,00",
        "tax_solidarity_enabled": "1",
        "tax_solidarity_rate": "5,50",
        "tax_church_enabled": "0",
        "tax_church_rate": "0,00",
        "tax_calculation_mode": "auto",
        "tax_rounding_mode": "commercial",
    }

    if request.method == "POST":
        values = {
            "tax_capital_enabled": "1" if request.form.get("tax_capital_enabled") == "1" else "0",
            "tax_capital_rate": request.form.get("tax_capital_rate", "25,00").strip() or "25,00",
            "tax_solidarity_enabled": "1" if request.form.get("tax_solidarity_enabled") == "1" else "0",
            "tax_solidarity_rate": request.form.get("tax_solidarity_rate", "5,50").strip() or "5,50",
            "tax_church_enabled": "1" if request.form.get("tax_church_enabled") == "1" else "0",
            "tax_church_rate": request.form.get("tax_church_rate", "0,00").strip() or "0,00",
            "tax_calculation_mode": request.form.get("tax_calculation_mode", "auto"),
            "tax_rounding_mode": request.form.get("tax_rounding_mode", "commercial"),
        }

        for key, value in values.items():
            setting = AppSetting.query.filter_by(key=key).first()
            if not setting:
                setting = AppSetting(key=key)
                db.session.add(setting)
            setting.value = value

        db.session.commit()
        flash("Finanzeinstellungen wurden gespeichert.", "success")
        return redirect(url_for("finance_settings"))

    settings = {}
    for key, default in defaults.items():
        setting = AppSetting.query.filter_by(key=key).first()
        settings[key] = setting.value if setting and setting.value is not None else default

    return render_template("finance_settings.html", settings=settings)

def parse_bank_statement_csv(document, max_rows=20):
    document_dir = Path(active_database_info()["documents_path"])
    file_path = document_dir / document.stored_filename

    if not file_path.exists():
        return {
            "ok": False,
            "message": "CSV-Datei wurde nicht gefunden.",
            "rows": [],
            "row_count": 0,
            "income_total_cents": 0,
            "expense_total_cents": 0,
        }

    raw = file_path.read_bytes()

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")

    reader = csv.DictReader(StringIO(text), delimiter=";")
    rows = []
    income_total_cents = 0
    expense_total_cents = 0

    for index, row in enumerate(reader, start=1):
        date_raw = (row.get("Datum") or "").strip()
        text_raw = (row.get("Buchungstext") or "").strip()
        purpose_raw = (row.get("Verwendungszweck") or "").strip()
        amount_raw = (row.get("Betrag") or "0").strip()

        try:
            sign = -1 if amount_raw.startswith("-") else 1
            amount_clean = amount_raw.lstrip("+-").strip()
            amount_cents = euro_to_cents(amount_clean) * sign
        except ValueError:
            rows.append({
                "index": index,
                "date": date_raw,
                "text": text_raw,
                "purpose": purpose_raw,
                "amount_cents": 0,
                "amount_euro": "0,00",
                "type": "Fehler",
                "error": f"Ungültiger Betrag: {amount_raw}",
            })
            continue

        if amount_cents >= 0:
            income_total_cents += amount_cents
        else:
            expense_total_cents += abs(amount_cents)

        if len(rows) < max_rows:
            rows.append({
                "index": index,
                "date": date_raw,
                "text": text_raw,
                "purpose": purpose_raw,
                "amount_cents": amount_cents,
                "amount_euro": cents_to_euro(amount_cents),
                "type": "Eingang" if amount_cents >= 0 else "Ausgang",
            })

    return {
        "ok": True,
        "message": "CSV wurde gelesen.",
        "rows": rows,
        "row_count": index if "index" in locals() else 0,
        "income_total_cents": income_total_cents,
        "expense_total_cents": expense_total_cents,
        "income_total_euro": cents_to_euro(income_total_cents),
        "expense_total_euro": cents_to_euro(expense_total_cents),
    }

@app.route("/finance/monthly-bank-closing", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def monthly_bank_closing():
    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    today = datetime.today().date()
    closing_month_date = today.replace(day=1) - timedelta(days=1)
    month_value = request.args.get("month") or closing_month_date.strftime("%Y-%m")
    year, month = parse_month_param(month_value)

    period_start = date(year, month, 1)
    period_end = date(year, month, last_day_of_month(year, month))

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "upload_statement":
            statement_file = request.files.get("statement_file")

            if not statement_file or not statement_file.filename:
                flash("Bitte eine Kontoauszug-Datei auswählen.", "danger")
                return redirect(url_for("monthly_bank_closing", month=f"{year:04d}-{month:02d}"))

            original_filename = secure_filename(statement_file.filename)
            suffix = original_filename.rsplit(".", 1)[1].lower() if "." in original_filename else ""

            if suffix not in {"pdf", "csv"}:
                flash("Erlaubt sind nur PDF- oder CSV-Dateien.", "danger")
                return redirect(url_for("monthly_bank_closing", month=f"{year:04d}-{month:02d}"))

            document_dir = Path(active_database_info()["documents_path"])
            document_dir.mkdir(parents=True, exist_ok=True)

            stored_filename = (
                f"kontoauszug_{year:04d}_{month:02d}_"
                f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_"
                f"{secrets.token_hex(6)}.{suffix}"
            )
            target = document_dir / stored_filename
            statement_file.save(target)

            document_category = "Kontoauszug PDF" if suffix == "pdf" else "Kontoauszug CSV"

            old_documents = (
                Document.query
                .filter(Document.category == document_category)
                .filter(Document.document_date == period_start)
                .filter(Document.deleted_at.is_(None))
                .all()
            )

            for old_document in old_documents:
                old_document.deleted_at = datetime.utcnow()
                old_document.deleted_by_user_id = current_user.id

            document = Document(
                title=f"{document_category} {month:02d}.{year}",
                category=document_category,
                document_date=period_start,
                description=f"Kontoauszug für Monatsabschluss {month:02d}.{year}",
                original_filename=original_filename,
                stored_filename=stored_filename,
                mime_type=statement_file.mimetype,
                file_size=target.stat().st_size,
                uploaded_by_user_id=current_user.id,
            )
            db.session.add(document)

            audit_log(
                "finance",
                "bank_statement_uploaded",
                f"Kontoauszug {month:02d}.{year} hochgeladen",
                details=f"Datei: {original_filename}\nGespeichert als: {stored_filename}",
                object_type="Document",
                new_value=original_filename,
            )

            db.session.commit()
            flash("Kontoauszug wurde hochgeladen.", "success")
            return redirect(url_for("monthly_bank_closing", month=f"{year:04d}-{month:02d}"))

    contribution_batch = MonthlyContributionBatch.query.filter_by(year=year, month=month).first()
    contribution_rows = monthly_contribution_rows(year, month, contribution_batch)

    contribution_member_count = len(contribution_rows)
    contribution_paid_count = sum(
        1
        for row in contribution_rows
        if row["expected_cents"] > 0 and row["paid_cents"] >= row["expected_cents"]
    )
    contribution_total_paid_cents = sum(row["paid_cents"] for row in contribution_rows)
    contribution_done = (
        contribution_batch is not None
        and contribution_batch.status == "finalized"
        and contribution_member_count > 0
        and contribution_paid_count == contribution_member_count
    )

    interest_booking = (
        InterestBooking.query
        .filter(InterestBooking.period_start == period_start)
        .filter(InterestBooking.period_end == period_end)
        .filter(InterestBooking.is_cancelled == False)
        .order_by(InterestBooking.booking_date.desc(), InterestBooking.created_at.desc())
        .first()
    )
    interest_done = interest_booking is not None

    statement_pdf_document = (
        Document.query
        .filter(Document.category == "Kontoauszug PDF")
        .filter(Document.document_date == period_start)
        .filter(Document.deleted_at.is_(None))
        .order_by(Document.uploaded_at.desc())
        .first()
    )

    statement_csv_document = (
        Document.query
        .filter(Document.category == "Kontoauszug CSV")
        .filter(Document.document_date == period_start)
        .filter(Document.deleted_at.is_(None))
        .order_by(Document.uploaded_at.desc())
        .first()
    )
    

    statement_document = statement_pdf_document
    statement_uploaded = statement_pdf_document is not None

    csv_preview = None

    if statement_csv_document:
        csv_preview = parse_bank_statement_csv(statement_csv_document)

    checks = {
        "statement": {
            "label": "Kontoauszug",
            "done": statement_uploaded,
            "status": "vorhanden" if statement_uploaded else "fehlt",
            "url": None,
        },
        "contributions": {
            "label": "Monatsbeiträge",
            "done": contribution_done,
            "status": f"{contribution_paid_count} / {contribution_member_count} erledigt",
            "url": url_for("monthly_contributions", month=f"{year:04d}-{month:02d}"),
        },
        "interest": {
            "label": "Zinsen",
            "done": interest_done,
            "status": "gebucht" if interest_done else "offen",
            "url": url_for("interest_module"),
        },
    }

    validation_checks = [
        {
            "icon": "📄",
            "title": "Kontoauszug vorhanden",
            "ok": checks["statement"]["done"],
            "message": "Kontoauszug wurde hochgeladen." if checks["statement"]["done"] else "Kontoauszug fehlt.",
        },
        {
            "icon": "💶",
            "title": "Monatsbeiträge",
            "ok": checks["contributions"]["done"],
            "message": f"{contribution_paid_count} von {contribution_member_count} Beiträgen verbucht.",
        },
        {
            "icon": "📈",
            "title": "Zinsen",
            "ok": checks["interest"]["done"],
            "message": "Zinsgutschrift vorhanden." if checks["interest"]["done"] else "Noch keine Zinsgutschrift.",
        },
    ]

    passed_validation_count = sum(1 for item in validation_checks if item["ok"])
    validation_count = len(validation_checks)
    validation_done = passed_validation_count == validation_count and validation_count > 0

    all_required_done = all(item["done"] for item in checks.values()) and validation_done

    next_step = None
    for key in ("statement", "contributions", "interest"):
        if not checks[key]["done"]:
            next_step = checks[key]
            break

    return render_template(
        "monthly_bank_closing.html",
        year=year,
        month=month,
        month_value=f"{year:04d}-{month:02d}",
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        contribution_done=contribution_done,
        contribution_paid_count=contribution_paid_count,
        contribution_member_count=contribution_member_count,
        contribution_total_paid=cents_to_euro(contribution_total_paid_cents),
        interest_done=interest_done,
        checks=checks,
        all_required_done=all_required_done,
        next_step=next_step,
        validation_checks=validation_checks,
        passed_validation_count=passed_validation_count,
        validation_count=validation_count,
        validation_done=validation_done,
        statement_pdf_document=statement_pdf_document,
        statement_csv_document=statement_csv_document,
        statement_document=statement_document,
        csv_preview=csv_preview,
    )

@app.route("/finance/monthly-contributions", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def monthly_contributions():
    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    year, month = parse_month_param(request.values.get("month"))
    period_value = f"{year:04d}-{month:02d}"
    target_date = first_day_of_month(year, month)

    batch = MonthlyContributionBatch.query.filter_by(year=year, month=month).first()

    if request.method == "POST":
        action = request.form.get("action", "save")
        account = request.form.get("account", "bank")
        if account not in ["cash", "bank"]:
            flash("Bitte Barkasse oder Bank auswählen.", "danger")
            return redirect(url_for("monthly_contributions", month=period_value))

        if batch and batch.status == "finalized":
            flash("Dieser Monat ist bereits verbucht und kann nicht erneut geändert werden.", "warning")
            return redirect(url_for("monthly_contributions", month=period_value))

        if not batch:
            batch = MonthlyContributionBatch(
                year=year,
                month=month,
                account=account,
                status="draft",
                created_by_user_id=current_user.id,
            )
            db.session.add(batch)
            db.session.flush()
        else:
            batch.account = account

        active_members = Member.query.filter_by(active=True).all()
        existing = {payment.member_id: payment for payment in batch.payments}
        default_expected = current_rate_cents("monthly_fee", target_date)

        confirm_member_id = None
        reset_member_id = None
        if action.startswith("confirm_"):
            try:
                confirm_member_id = int(action.split("_", 1)[1])
            except ValueError:
                confirm_member_id = None
        if action.startswith("reset_"):
            try:
                reset_member_id = int(action.split("_", 1)[1])
            except ValueError:
                reset_member_id = None

        processed_payments = []

        for member in active_members:
            try:
                expected_cents = euro_to_cents(request.form.get(f"expected_{member.id}", cents_to_euro(default_expected)))
                paid_cents = euro_to_cents(request.form.get(f"paid_{member.id}", "0"))
            except ValueError:
                flash(f"Bitte bei {member.short_name()} nur gültige Geldbeträge eingeben.", "danger")
                return redirect(url_for("monthly_contributions", month=period_value))

            paid_date_raw = request.form.get(f"paid_date_{member.id}", "").strip()
            try:
                paid_date = datetime.strptime(paid_date_raw, "%Y-%m-%d").date() if paid_date_raw else None
            except ValueError:
                paid_date = None

            note = request.form.get(f"note_{member.id}", "").strip() or None
            payment = existing.get(member.id)
            if not payment:
                payment = MonthlyContributionPayment(
                    batch_id=batch.id,
                    member_id=member.id,
                )
                db.session.add(payment)

            payment.expected_cents = expected_cents
            payment.paid_cents = paid_cents
            payment.paid_date = paid_date
            payment.note = note

            if paid_date and paid_cents > 0:
                update_member_monthly_value_day(member, paid_date, year, month)

            if confirm_member_id == member.id:
                payment.paid_cents = expected_cents
                payment.paid_date = (
                    paid_date
                    or proposed_member_paid_date(member.id, year, month)
                    or datetime.today().date()
                )
                update_member_monthly_value_day(member, payment.paid_date, year, month)
                audit_log(
                    "finance",
                    "monthly_contribution_confirmed",
                    f"Monatsbeitrag {month_label(year, month)} bestätigt",
                    details=f"Mitglied: {member.display_name()}; Betrag: {cents_to_euro(payment.paid_cents)} €; Wertstellung: {payment.paid_date}",
                    object_type="MonthlyContributionPayment",
                    object_id=payment.id,
                )

            if reset_member_id == member.id and current_user.role == "admin":
                audit_log(
                    "finance",
                    "monthly_contribution_reset",
                    f"Monatsbeitrag {month_label(year, month)} zurückgesetzt",
                    details=f"Mitglied: {member.display_name()}",
                    object_type="MonthlyContributionPayment",
                    object_id=payment.id,
                )
                payment.paid_cents = 0
                payment.paid_date = None
                payment.note = note

            processed_payments.append(payment)

        db.session.flush()

        # Monat automatisch abschließen, aber nur wenn wirklich jedes aktive Mitglied
        # einen eigenen bestätigten Zahlungseintrag mit mindestens Sollbetrag hat.
        # Wichtig: Nicht batch.payments verwenden, weil diese Collection während der
        # laufenden Session bei neuen Einträgen unvollständig sein kann.
        all_confirmed = bool(active_members) and len(processed_payments) == len(active_members) and all(
            (payment.expected_cents or 0) > 0
            and (payment.paid_cents or 0) >= (payment.expected_cents or 0)
            and payment.paid_date is not None
            for payment in processed_payments
        )

        if all_confirmed:
            total_paid = 0
            for payment in processed_payments:
                if payment.paid_cents > 0:
                    total_paid += payment.paid_cents
                    member_name = payment.member.display_name() if payment.member else "Mitglied"
                    booking_date = getattr(payment, "paid_date", None) or target_date
                    db.session.add(CashbookEntry(
                        booking_date=booking_date,
                        direction="income",
                        account=account,
                        amount_cents=payment.paid_cents,
                        category="Mitgliedsbeitrag",
                        person=member_name,
                        reason=f"Monatsbeitrag {month_label(year, month)}",
                        note=payment.note,
                        created_by_user_id=current_user.id,
                    ))
                    db.session.add(AccountTransaction(
                        account=account,
                        category="monthly_contribution",
                        amount_cents=payment.paid_cents,
                        booking_date=booking_date,
                        description=f"Monatsbeitrag {month_label(year, month)} - {member_name}",
                    ))
            batch.status = "finalized"
            batch.finalized_by_user_id = current_user.id
            batch.finalized_at = datetime.utcnow()
            audit_log(
                "finance",
                "monthly_contribution_finalized",
                f"Monatsbeiträge {month_label(year, month)} automatisch verbucht",
                details=f"Gesamt: {cents_to_euro(total_paid)} €",
                object_type="MonthlyContributionBatch",
                object_id=batch.id,
            )
            db.session.commit()
            flash(f"Alle Monatsbeiträge {month_label(year, month)} sind bestätigt und wurden automatisch verbucht: {cents_to_euro(total_paid)} €.", "success")
            return redirect(url_for("monthly_contributions", month=period_value))

        db.session.commit()
        if confirm_member_id:
            flash("Zahlung wurde bestätigt.", "success")
        elif reset_member_id:
            flash("Zahlung wurde zurückgesetzt.", "success")
        else:
            flash("Monatsbeiträge wurden als Prüfung gespeichert.", "success")
        return redirect(url_for("monthly_contributions", month=period_value))

    rows = monthly_contribution_rows(year, month, batch)
    total_expected = sum(row["expected_cents"] for row in rows)
    total_paid = sum(row["paid_cents"] for row in rows)
    total_open = max(total_expected - total_paid, 0)
    average_paid = int(round(total_paid / len(rows))) if rows else 0
    paid_count = sum(1 for row in rows if row["paid_cents"] >= row["expected_cents"] and row["expected_cents"] > 0)

    previous_batches = (
        MonthlyContributionBatch.query
        .order_by(MonthlyContributionBatch.year.desc(), MonthlyContributionBatch.month.desc())
        .limit(12)
        .all()
    )

    return render_template(
        "monthly_contributions.html",
        batch=batch,
        rows=rows,
        month_value=period_value,
        month_label=month_label(year, month),
        today=datetime.today().date().isoformat(),
        account=(batch.account if batch else "bank"),
        total_expected=cents_to_euro(total_expected),
        total_paid=cents_to_euro(total_paid),
        total_open=cents_to_euro(total_open),
        average_paid=cents_to_euro(average_paid),
        paid_count=paid_count,
        member_count=len(rows),
        previous_batches=previous_batches,
    )


@app.route("/audit-log")
@login_required
@role_required("admin", "cashier", "auditor")
def audit_log_page():
    category = request.args.get("category", "").strip()
    user = request.args.get("user", "").strip()
    q = request.args.get("q", "").strip()

    query = AuditLog.query
    if category:
        query = query.filter(AuditLog.category == category)
    if user:
        query = query.filter(AuditLog.username == user)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                AuditLog.title.ilike(like),
                AuditLog.details.ilike(like),
                AuditLog.old_value.ilike(like),
                AuditLog.new_value.ilike(like),
            )
        )

    entries = query.order_by(AuditLog.created_at.desc()).limit(300).all()
    categories = [
        ("", "Alle Bereiche"),
        ("finance", "Finanzen"),
        ("event", "Kegelabende"),
        ("member", "Mitglieder"),
        ("penalty_type", "Strafarten"),
        ("settings", "Einstellungen"),
        ("documents", "Dokumente"),
        ("backup", "Backups"),
        ("system", "System"),
    ]
    users = [row[0] for row in db.session.query(AuditLog.username).filter(AuditLog.username.isnot(None)).distinct().order_by(AuditLog.username).all()]
    return render_template(
        "audit_log.html",
        entries=entries,
        categories=categories,
        users=users,
        selected_category=category,
        selected_user=user,
        q=q,
    )


@app.route("/reports")
@login_required
def reports():
    year_raw = request.args.get("year", "all")
    selected_year = None
    if year_raw != "all":
        try:
            selected_year = int(year_raw)
        except ValueError:
            selected_year = None

    closed_events = report_event_query(selected_year).count()
    cancelled_query = BowlingEvent.query.filter(BowlingEvent.status == "cancelled")
    if selected_year:
        cancelled_query = cancelled_query.filter(db.extract("year", BowlingEvent.event_date) == selected_year)

    pump_rows = build_count_stat_rows(selected_year, "penalty_pump")
    wreath_rows = build_count_stat_rows(selected_year, "penalty_wreath")
    absence_rows = build_absence_stat_rows(selected_year)
    money_rows = build_penalty_money_rows(selected_year)
    payment_rows, payment_total_euro, payment_average_euro = member_payment_summary(selected_year)
    player_rows = build_player_overview_rows(selected_year)
    event_rows = build_event_overview_rows(selected_year)
    report_totals = build_report_totals(player_rows, event_rows, selected_year)

    highest_event_rows = sorted(event_rows, key=lambda row: row["penalty_sum_cents"], reverse=True)[:10]
    lowest_event_rows = sorted([row for row in event_rows if row["penalty_sum_cents"] > 0], key=lambda row: row["penalty_sum_cents"])[:10]

    return render_template(
        "reports.html",
        years=report_year_options(),
        selected_year=selected_year,
        selected_year_value=str(selected_year) if selected_year else "all",
        closed_events=closed_events,
        cancelled_events=cancelled_query.count(),
        pump_rows=pump_rows,
        wreath_rows=wreath_rows,
        absence_rows=absence_rows,
        money_rows=money_rows,
        payment_rows=payment_rows,
        payment_total_euro=payment_total_euro,
        payment_average_euro=payment_average_euro,
        player_rows=player_rows,
        event_rows=event_rows,
        report_totals=report_totals,
        most_penalties_rows=top_rows(player_rows, "penalty_total_cents"),
        least_penalties_rows=top_rows(player_rows, "penalty_total_cents", reverse=False),
        highest_single_rows=top_rows(player_rows, "highest_single_cents"),
        most_attended_rows=top_rows(player_rows, "attended"),
        most_open_rows=top_rows(player_rows, "open_cents"),
        most_credit_rows=top_rows(player_rows, "credit_cents"),
        highest_event_rows=highest_event_rows,
        lowest_event_rows=lowest_event_rows,
    )






@app.route("/exports")
@login_required
@role_required("admin", "cashier", "auditor")
def exports_page():
    return render_template(
        "exports.html",
        years=export_year_options(),
        current_year=datetime.now().year,
    )


@app.route("/exports/download")
@login_required
@role_required("admin", "cashier", "auditor")
def exports_download():
    export_type = request.args.get("type", "cashbook")
    fmt = request.args.get("format", "excel")
    year = export_filter_year()
    account = request.args.get("account", "all")
    member_scope = request.args.get("member_scope", "active")

    if export_type == "cashbook":
        rows = cashbook_export_rows(year, account)
        title = "Kassenbuch Export"
    elif export_type == "members":
        rows = members_export_rows(member_scope)
        title = "Mitglieder Export"
    elif export_type == "penalty_balances":
        rows = penalty_balance_export_rows()
        title = "Strafkonten Export"
    elif export_type == "annual_closing":
        rows = annual_closing_export_rows(year)
        title = "Jahresabschluss Export"
    elif export_type == "statistics":
        rows = statistics_export_rows(year)
        title = "Statistiken Export"
    else:
        flash("Unbekannter Exportbereich.", "danger")
        return redirect(url_for("exports_page"))

    headers = export_headers(export_type)
    audit_log(
        "system",
        "export_created",
        f"Export erstellt: {title}",
        details=f"Bereich: {title}\nFormat: {fmt}\nJahr: {year or 'Alle'}\nDatensätze: {len(rows)}",
        object_type="Export",
        new_value=title,
    )
    db.session.commit()
    return export_response(rows, headers, export_type, fmt, title)


@app.route("/my-event")
@login_required
def my_event():
    payload = personal_event_payload()
    return render_template("my_event.html", payload=payload)


@app.route("/my-event/data")
@login_required
def my_event_data():
    return jsonify(personal_event_payload())


@app.route("/events")
@login_required
def events():
    event_list = BowlingEvent.query.order_by(BowlingEvent.event_date.desc()).all()

    active_event = (
        BowlingEvent.query
        .filter(BowlingEvent.status.in_(("open", "settlement", "lane_cost")))
        .order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc())
        .first()
    )

    return render_template(
        "events.html",
        events=event_list,
        active_event=active_event,
    )

@app.route("/events/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "member")
def event_new():
    today = next_event_date_from_rhythm()
    open_event = (
        BowlingEvent.query
        .filter(BowlingEvent.status.in_(("open", "settlement", "lane_cost")))
        .order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc())
        .first()
    )

    if request.method == "POST":
        event_date_raw = request.form.get("event_date", "").strip()
        note = request.form.get("note", "").strip()
        is_cancelled = request.form.get("is_cancelled") == "1"
        cancel_reason = request.form.get("cancel_reason", "").strip()
        cancel_note = request.form.get("cancel_note", "").strip()
        force_new = request.form.get("force_new") == "1"

        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()

        if open_event and not is_cancelled and not force_new:
            flash("Es gibt bereits einen offenen Kegelabend. Bitte öffne ihn oder bestätige bewusst, dass du einen neuen Abend anlegen möchtest.", "warning")
            return render_template(
                "event_form.html",
                event=None,
                today=event_date_raw or today.isoformat(),
                form_data=request.form,
                open_event=open_event,
                require_force_new=True,
            )

        if is_cancelled and not cancel_reason:
            flash("Bitte einen Grund angeben, warum der Kegelabend ausfällt.", "danger")
            return render_template(
                "event_form.html",
                event=None,
                today=event_date_raw or today.isoformat(),
                form_data=request.form,
                open_event=open_event,
            )

        if is_cancelled:
            event_note = cancel_reason if not cancel_note else f"{cancel_reason}: {cancel_note}"
        else:
            event_note = note or None

        event = BowlingEvent(
            event_date=event_date,
            lane_cost_cents=0,
            status="cancelled" if is_cancelled else "open",
            note=event_note,
        )

        db.session.add(event)
        db.session.flush()
        audit_log(
            "event",
            "event_created",
            ("Ausgefallener Kegelabend angelegt" if is_cancelled else "Kegelabend angelegt"),
            details=f"Datum: {event.event_date}" + (f"; Grund: {event.note}" if event.note else ""),
            object_type="BowlingEvent",
            object_id=event.id,
        )

        if not is_cancelled:
            active_members = Member.query.filter_by(active=True).order_by(Member.first_name, Member.last_name).all()
            for member in active_members:
                db.session.add(EventParticipant(
                    event_id=event.id,
                    member_id=member.id,
                    status="present",
                ))

        db.session.commit()

        if is_cancelled:
            flash("Ausgefallener Kegelabend wurde angelegt.", "success")
            return redirect(url_for("events"))

        flash("Kegelabend wurde angelegt.", "success")
        return redirect(url_for("event_detail", event_id=event.id))

    return render_template(
        "event_form.html",
        event=None,
        today=today.isoformat(),
        open_event=open_event,
    )


@app.route("/events/<int:event_id>", methods=["GET", "POST"])
@login_required
def event_detail(event_id):
    event = BowlingEvent.query.get_or_404(event_id)

    existing_count = EventParticipant.query.filter_by(event_id=event.id).count()
    if existing_count == 0 and event.status != "cancelled":
        active_members = Member.query.filter_by(active=True).order_by(Member.first_name, Member.last_name).all()
        for member in active_members:
            db.session.add(EventParticipant(
                event_id=event.id,
                member_id=member.id,
                status="present",
            ))
        db.session.commit()

    lock_allowed, active_lock = acquire_event_lock(event)

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "take_lock":
            if not can_override_event_lock():
                flash("Nur Admin oder Kassierer/-in dürfen eine Bearbeitungssperre übernehmen.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            EventEditLock.query.filter_by(event_id=event.id).delete()
            db.session.commit()
            acquire_event_lock(event)
            flash("Bearbeitungssperre wurde übernommen. Du kannst diesen Kegelabend jetzt bearbeiten.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if not lock_allowed:
            flash("Dieser Kegelabend wird gerade von einem anderen Benutzer bearbeitet. Du bist im Lesemodus.", "danger")
            return redirect(url_for("event_detail", event_id=event.id))

        if event.status in ("closed", "cancelled") and action != "reopen":
            flash("Dieser Kegelabend ist endgültig abgeschlossen oder ausgefallen und kann nicht mehr geändert werden.", "danger")
            return redirect(url_for("event_detail", event_id=event.id))

        if event.status != "open" and action in ("save", "add_guest", "start_close", "cancel_event"):
            flash("Die Erfassung ist bereits abgeschlossen. Änderungen an Strafen sind jetzt gesperrt.", "danger")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "autosave":
            if event.status != "open":
                return jsonify({"ok": False, "message": "Autosave ist nur bei offenen Kegelabenden möglich."}), 400

            try:
                save_event_participants(event)
            except ValueError as exc:
                db.session.rollback()
                return jsonify({"ok": False, "message": str(exc) or "Eingaben konnten nicht gespeichert werden."}), 400

            db.session.commit()
            return jsonify({
                "ok": True,
                "message": "✓ Änderungen automatisch gespeichert",
                "saved_at": datetime.now().strftime("%H:%M:%S"),
            })

        if action == "save":
            old_snapshot = event_audit_snapshot(event)
            try:
                save_event_participants(event)
            except ValueError:
                db.session.rollback()
                return redirect(url_for("event_detail", event_id=event.id))
            new_snapshot = event_audit_snapshot(event)
            audit_log(
                "event",
                "event_saved",
                f"Kegelabend vom {event.event_date} gespeichert",
                details=audit_diff_lines(old_snapshot, new_snapshot),
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            db.session.commit()
            flash("✓ Änderungen gespeichert", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "add_guest":
            # Beim Gast-Hinzufügen aktuelle Status-Auswahl mit übernehmen, damit
            # z. B. bereits gesetzte Fehlend-Status nicht verloren gehen.
            for participant in EventParticipant.query.filter_by(event_id=event.id).all():
                status = request.form.get(f"participant_{participant.id}_status")
                if status in ["present", "excused", "unexcused"] and participant.member_id:
                    participant.status = status

            if event_has_started_entries(event):
                flash("Gast hinzufügen ist nach den ersten Einträgen gesperrt.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            guest_name = request.form.get("guest_name", "").strip()

            if guest_name:
                guest = EventParticipant(
                    event_id=event.id,
                    guest_name=guest_name,
                    status="guest",
                )
                db.session.add(guest)
                db.session.commit()
                flash("Gast wurde hinzugefügt.", "success")

            return redirect(url_for("event_detail", event_id=event.id))

        if action == "cancel_event":
            old_snapshot = event_audit_snapshot(event)
            cancel_reason = request.form.get("cancel_reason", "").strip()
            cancel_note = request.form.get("cancel_note", "").strip()

            if not cancel_reason:
                flash("Bitte einen Grund für den ausgefallenen Kegelabend angeben.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            reset_event_bookings(event)
            clear_event_entry_values(event)
            event.lane_cost_cents = 0
            event.status = "cancelled"
            event.note = cancel_reason if not cancel_note else f"{cancel_reason}: {cancel_note}"
            new_snapshot = event_audit_snapshot(event)
            audit_log(
                "event",
                "event_cancelled",
                f"Kegelabend vom {event.event_date} als ausgefallen markiert",
                details=audit_diff_lines(old_snapshot, new_snapshot),
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            release_event_lock(event)
            db.session.commit()
            flash("Kegelabend wurde als ausgefallen markiert.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "start_close":
            old_snapshot = event_audit_snapshot(event)
            try:
                save_event_participants(event)
            except ValueError:
                db.session.rollback()
                return redirect(url_for("event_detail", event_id=event.id))
            event.status = "settlement"
            new_snapshot = event_audit_snapshot(event)
            audit_log(
                "event",
                "event_capture_finished",
                f"Strafenerfassung für Kegelabend vom {event.event_date} abgeschlossen",
                details=audit_diff_lines(old_snapshot, new_snapshot),
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            db.session.commit()
            flash("Bitte jetzt Barzahlungen erfassen. Danach werden die Bahnkosten eingetragen.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "reopen":
            if not can_edit_closed_events():
                flash("Nur Admin oder Kassierer/-in dürfen abgeschlossene oder ausgefallene Abende wieder öffnen.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            old_snapshot = event_audit_snapshot(event)
            event.status = "open"
            new_snapshot = event_audit_snapshot(event)
            audit_log(
                "event",
                "event_reopened",
                f"Kegelabend vom {event.event_date} wieder geöffnet",
                details=audit_diff_lines(old_snapshot, new_snapshot),
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            db.session.commit()
            flash("Kegelabend wurde wieder geöffnet. Bereits erfasste Zahlungen bleiben als Vorschlag erhalten und werden beim erneuten Verbuchen sauber neu gebucht.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "save_settlement":
            old_snapshot = event_audit_snapshot(event)
            settlement_detail_lines = []
            if event.status != "settlement":
                flash("Barzahlungen können nur im Abrechnungs-Schritt erfasst werden.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            reset_event_bookings(event)

            participants = EventParticipant.query.filter_by(event_id=event.id).all()
            average_present_cents = event_average_present_penalty_cents(event)
            marker = f"Kegelabend #{event.id} vom {event.event_date}"

            for participant in participants:
                penalty_cents = participant_penalty_cents(
                    participant,
                    event.event_date,
                    average_present_cents,
                )

                if participant.member_id and penalty_cents:
                    db.session.add(MemberPenaltyTransaction(
                        member_id=participant.member_id,
                        event_id=event.id,
                        participant_id=participant.id,
                        category="event_penalty",
                        amount_cents=penalty_cents,
                        booking_date=event.event_date,
                        description=f"Strafen {marker}",
                    ))

                try:
                    paid_cents = form_euro_to_cents(f"paid_{participant.id}", f"Bezahlt für {participant.name()}")
                except ValueError:
                    db.session.rollback()
                    return redirect(url_for("event_detail", event_id=event.id))

                settlement_detail_lines.append(f"{participant.name()}: Strafen {cents_to_euro(penalty_cents)} €, bezahlt {cents_to_euro(paid_cents)} €")

                if paid_cents:
                    if participant.member_id:
                        db.session.add(MemberPenaltyTransaction(
                            member_id=participant.member_id,
                            event_id=event.id,
                            participant_id=participant.id,
                            category="cash_payment",
                            amount_cents=-paid_cents,
                            booking_date=event.event_date,
                            description=f"Barzahlung {marker}",
                        ))

                    db.session.add(AccountTransaction(
                        account="cash",
                        category="event_cash_payment",
                        amount_cents=paid_cents,
                        booking_date=event.event_date,
                        description=f"Barzahlung {participant.name()} - {marker}",
                    ))
                    db.session.add(CashbookEntry(
                        booking_date=event.event_date,
                        direction="income",
                        account="cash",
                        amount_cents=paid_cents,
                        category="Barzahlung Strafen",
                        person=participant.name(),
                        reason=f"Barzahlung Strafen Kegelabend {event.event_date}",
                        note=f"Automatisch aus Kegelabend-Abrechnung: {marker}",
                        created_by_user_id=current_user.id,
                    ))

            event.status = "lane_cost"
            new_snapshot = event_audit_snapshot(event)
            detail_text = audit_diff_lines(old_snapshot, new_snapshot)
            if settlement_detail_lines:
                detail_text += "\nBarzahlungen:\n" + "\n".join(settlement_detail_lines)
            audit_log(
                "event",
                "event_settlement_saved",
                f"Barzahlungen für Kegelabend vom {event.event_date} verbucht",
                details=detail_text,
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            db.session.commit()
            flash("Barzahlungen wurden verbucht. Jetzt können die Bahnkosten eingetragen werden.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "final_close":
            old_snapshot = event_audit_snapshot(event)
            if event.status != "lane_cost":
                flash("Bitte zuerst die Barzahlungen verbuchen. Danach werden die Bahnkosten erfasst.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            try:
                event.lane_cost_cents = form_euro_to_cents("lane_cost", "Bahnkosten")
            except ValueError:
                db.session.rollback()
                return redirect(url_for("event_detail", event_id=event.id))

            marker = f"Kegelabend #{event.id} vom {event.event_date}"

            lane_cashbook_entry = None
            if event.lane_cost_cents:
                lane_cashbook_entry = CashbookEntry(
                    booking_date=event.event_date,
                    direction="expense",
                    account="cash",
                    amount_cents=event.lane_cost_cents,
                    category="Bahnkosten",
                    person="Kegelbahn",
                    reason=f"Bahnkosten Kegelabend {event.event_date}",
                    note=f"Automatisch beim Abschluss gebucht: {marker}",
                    created_by_user_id=current_user.id,
                )
                db.session.add(lane_cashbook_entry)
                db.session.flush()
                db.session.add(AccountTransaction(
                    account="cash",
                    category="lane_cost",
                    amount_cents=-event.lane_cost_cents,
                    booking_date=event.event_date,
                    description=f"Bahnkosten {marker}",
                ))

            receipt_file = request.files.get("lane_receipt_file")
            if receipt_file and receipt_file.filename:
                if document_allowed(receipt_file.filename):
                    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
                    original_filename = secure_filename(receipt_file.filename)
                    suffix = original_filename.rsplit(".", 1)[1].lower() if "." in original_filename else "bin"
                    stored_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(8)}.{suffix}"
                    target = DOCUMENT_DIR / stored_filename
                    receipt_file.save(target)
                    document = Document(
                        title=f"Kegelbahn-Rechnung {event.event_date.strftime('%d.%m.%Y')}",
                        category="Kegelbahn-Rechnung",
                        document_date=event.event_date,
                        description=f"Beim Abschluss des Kegelabends hochgeladener Bahnkosten-Beleg über {cents_to_euro(event.lane_cost_cents)} €.",
                        original_filename=original_filename,
                        stored_filename=stored_filename,
                        mime_type=receipt_file.mimetype,
                        file_size=target.stat().st_size if target.exists() else 0,
                        event_id=event.id,
                        cashbook_entry_id=lane_cashbook_entry.id if lane_cashbook_entry else None,
                        uploaded_by_user_id=current_user.id,
                    )
                    db.session.add(document)
                else:
                    flash("Bahnkosten wurden gespeichert, aber der Beleg-Dateityp ist nicht erlaubt.", "warning")

            event.status = "closed"
            new_snapshot = event_audit_snapshot(event)
            audit_log(
                "event",
                "event_closed",
                f"Kegelabend vom {event.event_date} endgültig abgeschlossen",
                details=audit_diff_lines(old_snapshot, new_snapshot),
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            release_event_lock(event)
            db.session.commit()
            flash(f"Kegelabend wurde abgeschlossen. Barkasse jetzt: {cents_to_euro(account_balance('cash'))} €", "success")
            return redirect(url_for("event_detail", event_id=event.id))

    participants = EventParticipant.query.filter_by(event_id=event.id).all()
    penalty_types = active_penalty_types()
    participant_rows = []

    average_present_raw_cents = event_average_present_penalty_raw_cents(event)
    average_present_cents = event_average_present_penalty_cents(event)
    guest_fee_cents = current_rate_cents("guest_fee", event.event_date)
    absence_excused_cents = current_rate_cents("absence_excused", event.event_date)
    absence_unexcused_cents = current_rate_cents("absence_unexcused", event.event_date)
    lane_cost_default_cents = current_rate_cents("lane_cost_default", event.event_date)

    paid_by_participant = {}
    payment_transactions = MemberPenaltyTransaction.query.filter_by(event_id=event.id, category="cash_payment").all()
    for transaction in payment_transactions:
        if transaction.participant_id:
            paid_by_participant[transaction.participant_id] = paid_by_participant.get(transaction.participant_id, 0) + abs(transaction.amount_cents or 0)

    for participant in participants:
        penalty_values = {}
        for penalty_type in penalty_types:
            penalty = get_or_create_participant_penalty(participant, penalty_type)
            penalty_values[penalty_type.id] = penalty

        penalty_cents = participant_penalty_cents(
            participant,
            event.event_date,
            average_present_cents,
        )
        previous_balance_cents = member_penalty_balance(participant.member_id) if participant.member_id else 0
        # Während der Erfassung/Abrechnung sind die aktuellen Strafen noch nicht endgültig gebucht,
        # deshalb werden sie zum bisherigen Strafkonto addiert. Nach den Barzahlungen bzw. nach
        # endgültigem Abschluss enthält member_penalty_balance() bereits Strafen minus Zahlungen.
        if participant.member_id and event.status in ("lane_cost", "closed"):
            balance_cents = previous_balance_cents
        else:
            balance_cents = previous_balance_cents + penalty_cents if participant.member_id else 0
        participant_rows.append({
            "participant": participant,
            "display_name": participant_display_name(participant),
            "penalty_values": penalty_values,
            "penalty_cents": penalty_cents,
            "penalty_euro": cents_to_euro(penalty_cents),
            "previous_balance_cents": previous_balance_cents if participant.member_id else 0,
            "previous_balance_euro": cents_to_euro(previous_balance_cents) if participant.member_id else "0,00",
            "balance_cents": balance_cents,
            "balance_euro": cents_to_euro(balance_cents),
            "paid_cents": paid_by_participant.get(participant.id, 0),
            "paid_euro": cents_to_euro(paid_by_participant.get(participant.id, 0)),
        })

    participant_rows.sort(key=participant_sort_key)
    settlement_rows = sorted(participant_rows, key=lambda row: (participant_sort_name(row["participant"]), row["display_name"].casefold()))

    db.session.commit()

    return render_template(
        "event_detail.html",
        event=event,
        participants=participants,
        participant_rows=participant_rows,
        settlement_rows=settlement_rows,
        penalty_types=penalty_types,
        can_edit_closed=can_edit_closed_events(),
        can_override_lock=can_override_event_lock(),
        lock_allowed=lock_allowed,
        active_lock=active_lock,
        cents_to_euro=cents_to_euro,
        average_present_raw_cents=average_present_raw_cents,
        average_present_raw_euro=cents_to_euro(average_present_raw_cents),
        average_present_cents=average_present_cents,
        average_present_euro=cents_to_euro(average_present_cents),
        average_rounding_text=average_rounding_label(average_present_raw_cents, average_present_cents),
        guest_fee_cents=guest_fee_cents,
        guest_fee_euro=cents_to_euro(guest_fee_cents),
        absence_excused_cents=absence_excused_cents,
        absence_excused_euro=cents_to_euro(absence_excused_cents),
        absence_unexcused_cents=absence_unexcused_cents,
        absence_unexcused_euro=cents_to_euro(absence_unexcused_cents),
        lane_cost_default_cents=lane_cost_default_cents,
        lane_cost_default_euro=cents_to_euro(lane_cost_default_cents),
        lane_cost_prefill_euro=cents_to_euro(event.lane_cost_cents or lane_cost_default_cents),
        current_cash_balance_cents=account_balance("cash"),
        current_cash_balance_euro=cents_to_euro(account_balance("cash")),
        allow_guest_add=(event.status == "open" and not event_has_started_entries(event)),
    )


@app.route("/events/<int:event_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def event_delete(event_id):
    event = BowlingEvent.query.get_or_404(event_id)
    reset_event_bookings(event)

    participants = EventParticipant.query.filter_by(event_id=event.id).all()
    for participant in participants:
        ParticipantPenalty.query.filter_by(participant_id=participant.id).delete()
        db.session.delete(participant)

    EventEditLock.query.filter_by(event_id=event.id).delete()
    event_date = event.event_date
    db.session.delete(event)
    audit_log(
        "event",
        "event_deleted",
        f"Kegelabend vom {event_date} gelöscht",
        object_type="BowlingEvent",
        object_id=event_id,
    )
    db.session.commit()

    flash("Kegelabend wurde gelöscht.", "success")
    return redirect(url_for("events"))


@app.route("/penalty-balances", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def penalty_balances():
    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    if request.method == "POST":
        member_id = int(request.form.get("member_id", 0) or 0)
        amount_raw = request.form.get("amount", "0").strip()
        booking_date_raw = request.form.get("booking_date", "").strip()
        note = request.form.get("note", "").strip()

        member = Member.query.get_or_404(member_id)
        try:
            amount_cents = form_euro_to_cents("amount", "Betrag")
        except ValueError:
            return redirect(url_for("penalty_balances"))
        booking_date = datetime.strptime(booking_date_raw, "%Y-%m-%d").date()

        if amount_cents <= 0:
            flash("Bitte einen positiven Zahlungsbetrag eingeben.", "danger")
            return redirect(url_for("penalty_balances"))

        description = note or "Überweisung Strafgeld"

        db.session.add(MemberPenaltyTransaction(
            member_id=member.id,
            category="bank_transfer",
            amount_cents=-amount_cents,
            booking_date=booking_date,
            description=description,
        ))

        db.session.add(AccountTransaction(
            account="bank",
            category="penalty_bank_transfer",
            amount_cents=amount_cents,
            booking_date=booking_date,
            description=f"Überweisung Strafgeld {member.display_name()}: {description}",
        ))

        audit_log(
            "finance",
            "penalty_bank_transfer",
            f"Strafgeld-Überweisung für {member.display_name()} gebucht",
            details=f"Betrag: {cents_to_euro(amount_cents)} €; Datum: {booking_date}; Grund: {description}",
            object_type="Member",
            object_id=member.id,
        )
        db.session.commit()
        flash("Überweisung wurde gebucht und dem Strafkonto gutgeschrieben.", "success")
        return redirect(url_for("penalty_balances"))

    members = Member.query.order_by(Member.active.desc(), Member.first_name, Member.last_name).all()
    rows = []

    for member in members:
        balance = member_penalty_balance(member.id)
        rows.append({
            "member": member,
            "balance_cents": balance,
            "balance_euro": cents_to_euro(abs(balance)),
            "status": "offen" if balance > 0 else "Guthaben" if balance < 0 else "ausgeglichen",
        })

    return render_template(
        "penalty_balances.html",
        rows=rows,
        members=members,
        today=datetime.today().date().isoformat(),
    )

def ensure_interest_booking_cancel_columns():
    inspector = db.inspect(db.engine)
    columns = {
        column["name"]
        for column in inspector.get_columns("interest_bookings")
    }

    statements = []

    if "reversal_cashbook_entry_id" not in columns:
        statements.append("ALTER TABLE interest_bookings ADD COLUMN reversal_cashbook_entry_id INTEGER")

    if "is_cancelled" not in columns:
        statements.append("ALTER TABLE interest_bookings ADD COLUMN is_cancelled BOOLEAN NOT NULL DEFAULT 0")

    if "cancelled_at" not in columns:
        statements.append("ALTER TABLE interest_bookings ADD COLUMN cancelled_at DATETIME")

    if "cancelled_by_user_id" not in columns:
        statements.append("ALTER TABLE interest_bookings ADD COLUMN cancelled_by_user_id INTEGER")

    if "cancel_reason" not in columns:
        statements.append("ALTER TABLE interest_bookings ADD COLUMN cancel_reason TEXT")

    for statement in statements:
        db.session.execute(db.text(statement))

    if statements:
        db.session.commit()

with app.app_context():
    db.create_all()
    ensure_interest_booking_cancel_columns()
    migrate_schema_extensions()

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
    app.run(host="0.0.0.0", port=5000)
