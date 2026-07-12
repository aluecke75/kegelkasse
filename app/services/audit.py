"""Revisionsprotokoll: Schreiben von Änderungsvermerken (audit_log) sowie
Formatierungs-/Snapshot-Helfer für Vorher/Nachher-Anzeigen."""
import re
from datetime import datetime, timedelta

from flask_login import current_user

from extensions import app
from models import db, AuditLog
from services.money import cents_to_euro

# Kategorien, die für die automatische Aufbewahrungsfrist (Revisionsprotokoll
# bereinigen) infrage kommen - rein operative Meldungen ohne dauerhaften
# Beweiswert für eine Kassenprüfung. Finanziell/rechtlich relevante Kategorien
# (finance, event, member, settings, penalty_type, annual_closing, Dokumente,
# interest) sind bewusst NICHT enthalten und bleiben von der Automatik immer
# unberührt, unabhängig vom Alter - es gibt keine zuverlässige Verknüpfung
# zwischen einem Revisionsprotokoll-Eintrag und einem abgeschlossenen
# Geschäftsjahr, deshalb lieber grundsätzlich ausnehmen als riskieren.
RETENTION_ELIGIBLE_CATEGORIES = {"Datensicherung", "system", "Vereins-Import", "Vereins-Export"}


def count_old_audit_log_entries(retention_days):
    """Wie cleanup_old_audit_log_entries(), zählt aber nur, ohne zu löschen.

    Wichtig für Aufrufer, die selbst noch einen "X Einträge gelöscht"-
    Revisionsprotokoll-Eintrag schreiben wollen: der muss VOR dem
    tatsächlichen Löschen erzeugt werden (siehe cleanup_old_audit_log_entries).
    """
    if not retention_days or retention_days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    return AuditLog.query.filter(
        AuditLog.category.in_(RETENTION_ELIGIBLE_CATEGORIES),
        AuditLog.created_at < cutoff,
    ).count()


def cleanup_old_audit_log_entries(retention_days):
    """Löscht Revisionsprotokoll-Einträge aus den "operativen" Kategorien
    (siehe RETENTION_ELIGIBLE_CATEGORIES), die älter als retention_days sind.

    Committet NICHT selbst - Aufrufer ist dafür verantwortlich. Gibt die
    Anzahl gelöschter Zeilen zurück.

    Wer danach noch selbst einen audit_log()-Eintrag für diese Bereinigung
    schreiben will, MUSS das VOR diesem Aufruf tun (siehe
    count_old_audit_log_entries): die Tabelle nutzt SQLite-Rowids ohne
    AUTOINCREMENT, die nach dem Löschen der zuvor höchsten ID sofort wieder
    vergeben werden - ein danach eingefügter Eintrag würde sonst exakt die ID
    einer gerade gelöschten Zeile "erben" und beim Nachschlagen wie ein nie
    gelöschter Eintrag aussehen.
    """
    if not retention_days or retention_days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    deleted = AuditLog.query.filter(
        AuditLog.category.in_(RETENTION_ELIGIBLE_CATEGORIES),
        AuditLog.created_at < cutoff,
    ).delete(synchronize_session=False)
    return deleted


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


_AUDIT_OBJECT_LABELS = {
    "Member": "Mitglied",
    "BowlingEvent": "Kegelabend",
    "PenaltyType": "Strafart",
    "CashbookEntry": "Kassenbuch-Eintrag",
    "CashAudit": "Kassenprüfung",
    "AnnualClosing": "Jahresabschluss",
    "InterestSetting": "Zinseinstellung",
    "InterestBooking": "Zinsbuchung",
    "document": "Dokument",
    "Document": "Dokument",
    "Branding": "Vereinslogo",
    "Backup": "Datensicherung",
    "Export": "Export",
    "MonthlyContributionPayment": "Monatsbeitrag",
}


def audit_object_label(object_type, object_id):
    if not object_type:
        return None
    label = _AUDIT_OBJECT_LABELS.get(object_type, object_type)
    return f"{label} #{object_id}" if object_id else label
