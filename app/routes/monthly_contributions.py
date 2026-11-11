"""Monatsabschluss: Monatsbeiträge erfassen/verbuchen und der Monatsabschluss-Assistent
(Kontoauszug hochladen, Checkliste Beiträge/Zinsen)."""
import csv
import secrets
from datetime import datetime, date, timedelta
from io import StringIO
from pathlib import Path

from flask import request, flash, redirect, url_for, render_template, session
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import app
from auth import role_required
from models import db, Member, MonthlyContributionBatch, MonthlyContributionPayment, Document, InterestBooking, CashbookEntry, AccountTransaction
from services.dates import parse_month_param, month_label, first_day_of_month, shift_weekend_to_monday, last_day_of_month
from services.money import cents_to_euro, euro_to_cents
from services.audit import audit_log
from services.csv_import_matching import suggest_for_row, learn_from_confirmation, monthly_contribution_duplicate_hint
from services.pdf_import import parse_bank_statement_pdf
from routes.cashbook import cashbook_category_options
from routes.interest import (
    book_interest_from_statement,
    active_interest_booking_for_period,
    resolve_interest_breakdown,
)

# Deutlich über dem, was ein Kegelklub-Konto in einem Monat realistisch an
# Buchungszeilen hat - stellt aber sicher, dass parse_bank_statement_csv()
# für den Buchungen-übernehmen-Assistenten wirklich ALLE Zeilen liefert und
# nicht (wie beim reinen Vorschau-Panel) nach den ersten paar abschneidet.
CSV_IMPORT_ROW_LIMIT = 5000

_BANK_STATEMENT_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y")


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


# -----------------------------------------------------------------------------
# Monatsbeiträge <-> Kassenbuch (Kontoauszug-Import)
# -----------------------------------------------------------------------------
#
# Ein aus dem Kontoauszug übernommener "Mitgliedsbeitrag" steht sofort im
# Kassenbuch (und auf dem Bankkonto). Damit Schritt 2 "Monatsbeiträge" ihn als
# bezahlt zählt, wird er zusätzlich als Monatsbeitrags-Zahlung eingetragen und
# über CashbookEntry.monthly_contribution_payment_id verknüpft. Beim Abschluss
# des Monats wird je Mitglied nur noch der Teil gebucht, der nicht schon über
# verknüpfte Kassenbuch-Einträge gebucht ist - sonst stünde der Beitrag doppelt
# im Kassenbuch.

def imported_contribution_summary(payment_id):
    """(Betrag in Cent, frühestes Buchungsdatum) der aktiven Kassenbuch-Einträge,
    die bereits zu dieser Monatsbeitrags-Zahlung gebucht sind."""
    if not payment_id:
        return 0, None
    total, first_date = (
        db.session.query(
            db.func.coalesce(db.func.sum(CashbookEntry.amount_cents), 0),
            db.func.min(CashbookEntry.booking_date),
        )
        .filter(CashbookEntry.monthly_contribution_payment_id == payment_id)
        .filter(CashbookEntry.is_void == False)  # noqa: E712
        .filter(CashbookEntry.direction == "income")
        .one()
    )
    if isinstance(first_date, str):
        first_date = datetime.strptime(first_date, "%Y-%m-%d").date()
    return int(total or 0), first_date


def unlinked_imported_contribution_entries(year, month):
    """Aus dem Kontoauszug dieses Monats übernommene, aktive Einträge
    "Mitgliedsbeitrag", die noch keiner Monatsbeitrags-Zahlung zugeordnet sind."""
    return (
        CashbookEntry.query
        .join(Document, CashbookEntry.source_document_id == Document.id)
        .filter(Document.document_date == date(year, month, 1))
        .filter(Document.category.in_(("Kontoauszug CSV", "Kontoauszug PDF")))
        .filter(CashbookEntry.category == "Mitgliedsbeitrag")
        .filter(CashbookEntry.direction == "income")
        .filter(CashbookEntry.is_void == False)  # noqa: E712
        .filter(CashbookEntry.monthly_contribution_payment_id.is_(None))
        .order_by(CashbookEntry.booking_date, CashbookEntry.id)
        .all()
    )


def _active_members_by_display_name():
    """Anzeigename -> aktives Mitglied; mehrdeutige Namen bleiben außen vor."""
    grouped = {}
    for member in Member.query.filter_by(active=True).all():
        grouped.setdefault(member.display_name(), []).append(member)
    return {name: members[0] for name, members in grouped.items() if len(members) == 1}


def contribution_batch_complete(active_members, payments_by_member):
    """True, wenn jedes aktive Mitglied einen Zahlungseintrag mit mindestens
    Sollbetrag und Zahldatum hat (Sollbetrag 0 € gilt als erfüllt)."""
    payments = [payments_by_member.get(member.id) for member in active_members]
    return bool(active_members) and all(payment is not None for payment in payments) and all(
        (payment.expected_cents or 0) == 0
        or (
            (payment.paid_cents or 0) >= (payment.expected_cents or 0)
            and payment.paid_date is not None
        )
        for payment in payments
    )


def finalize_contribution_batch_if_complete(batch, active_members, payments_by_member, user_id):
    """Schließt den Monat ab, wenn jedes aktive Mitglied einen bestätigten
    Zahlungseintrag mit mindestens Sollbetrag hat (Sollbetrag 0 € gilt als erfüllt).

    Gebucht wird je Mitglied nur, was noch nicht über verknüpfte Kassenbuch-Einträge
    (Kontoauszug-Import) gebucht ist. Rückgabe: None, wenn der Monat noch nicht
    vollständig ist, sonst {"total_cents", "newly_booked_cents", "imported_cents"}.
    Committet nicht selbst.
    """
    year, month = batch.year, batch.month
    if not contribution_batch_complete(active_members, payments_by_member):
        return None
    payments = [payments_by_member[member.id] for member in active_members]

    total_paid = 0
    newly_booked = 0
    for payment in payments:
        if (payment.paid_cents or 0) <= 0:
            continue
        total_paid += payment.paid_cents
        already_booked, _ = imported_contribution_summary(payment.id)
        remainder = payment.paid_cents - already_booked
        if remainder <= 0:
            continue
        newly_booked += remainder
        member_name = payment.member.display_name() if payment.member else "Mitglied"
        booking_date = payment.paid_date or first_day_of_month(year, month)
        db.session.add(CashbookEntry(
            booking_date=booking_date,
            direction="income",
            account=batch.account,
            amount_cents=remainder,
            category="Mitgliedsbeitrag",
            person=member_name,
            reason=f"Monatsbeitrag {month_label(year, month)}",
            note=payment.note,
            created_by_user_id=user_id,
            monthly_contribution_payment_id=payment.id,
        ))
        db.session.add(AccountTransaction(
            account=batch.account,
            category="monthly_contribution",
            amount_cents=remainder,
            booking_date=booking_date,
            description=f"Monatsbeitrag {month_label(year, month)} - {member_name}",
        ))

    batch.status = "finalized"
    batch.finalized_by_user_id = user_id
    batch.finalized_at = datetime.utcnow()
    imported = total_paid - newly_booked
    details = f"Gesamt: {cents_to_euro(total_paid)} €"
    if imported:
        details += f"; davon bereits per Kontoauszug-Import gebucht: {cents_to_euro(imported)} €"
    audit_log(
        "finance",
        "monthly_contribution_finalized",
        f"Monatsbeiträge {month_label(year, month)} automatisch verbucht",
        details=details,
        object_type="MonthlyContributionBatch",
        object_id=batch.id,
    )
    return {"total_cents": total_paid, "newly_booked_cents": newly_booked, "imported_cents": imported}


def link_imported_contributions(year, month, user_id, finalize=True):
    """Trägt aus dem Kontoauszug übernommene Mitgliedsbeiträge des Monats als
    Monatsbeitrags-Zahlungen ein (siehe Abschnittskommentar oben). Mit
    finalize=True wird der Monat danach abgeschlossen, wenn alle aktiven
    Mitglieder bezahlt haben - aber nur im eindeutigen Fall (keine Hinweise,
    keine nicht zuordenbaren Buchungen), damit der Kassierer Auffälliges vor dem
    endgültigen Abschluss in Schritt 2 sieht.

    Zuordnung über den Anzeigenamen im Feld "Person" (so speichert ihn der Import).
    Ist der Monat bereits verbucht, wird nichts verknüpft (die Beiträge stehen dann
    möglicherweise doppelt im Kassenbuch - der Aufrufer meldet das).

    Rückgabe: {"linked", "linked_cents", "unmatched" (Einträge), "finalized_before"
    (Einträge), "warnings" (Texte), "finalized" (Ergebnis von
    finalize_contribution_batch_if_complete oder None), "finalize_held" (True, wenn
    nur wegen Hinweisen nicht abgeschlossen wurde)}. Committet nicht selbst.
    """
    from app import current_rate_cents

    result = {
        "linked": 0, "linked_cents": 0, "linked_member_ids": set(), "unmatched": [], "finalized_before": [],
        "warnings": [], "finalized": None, "finalize_held": False,
    }
    entries = unlinked_imported_contribution_entries(year, month)
    if not entries:
        return result

    batch = MonthlyContributionBatch.query.filter_by(year=year, month=month).first()
    if batch and batch.status == "finalized":
        result["finalized_before"] = entries
        return result

    members_by_name = _active_members_by_display_name()
    if not any((entry.person or "").strip() in members_by_name for entry in entries):
        result["unmatched"] = entries
        return result

    if not batch:
        batch = MonthlyContributionBatch(
            year=year, month=month, account="bank", status="draft", created_by_user_id=user_id,
        )
        db.session.add(batch)
        db.session.flush()

    default_expected = current_rate_cents("monthly_fee", first_day_of_month(year, month))
    payments = {
        payment.member_id: payment
        for payment in MonthlyContributionPayment.query.filter_by(batch_id=batch.id).all()
    }

    for entry in entries:
        member = members_by_name.get((entry.person or "").strip())
        if not member:
            result["unmatched"].append(entry)
            continue

        payment = payments.get(member.id)
        if not payment:
            payment = MonthlyContributionPayment(
                batch_id=batch.id, member_id=member.id, expected_cents=default_expected, paid_cents=0,
            )
            db.session.add(payment)
            db.session.flush()
            payments[member.id] = payment

        already_booked, _ = imported_contribution_summary(payment.id)
        manual_unbooked = (payment.paid_cents or 0) - already_booked
        entry.monthly_contribution_payment_id = payment.id
        # Bereits von Hand eingetragener (noch nicht verbuchter) Betrag und
        # Kontoauszug beschreiben normalerweise dieselbe Zahlung: den größeren
        # Wert nehmen statt zu addieren. Weitere Überweisungen desselben
        # Mitglieds im selben Monat summieren sich über already_booked.
        payment.paid_cents = max(payment.paid_cents or 0, already_booked + entry.amount_cents)
        if manual_unbooked > 0:
            result["warnings"].append(
                f"{member.display_name()}: In „Monatsbeiträge {month_label(year, month)}“ waren schon "
                f"{cents_to_euro(manual_unbooked)} € von Hand eingetragen, der Kontoauszug zeigt "
                f"{cents_to_euro(entry.amount_cents)} € – das wurde als dieselbe Zahlung gewertet. Falls "
                "zusätzlich bar bezahlt wurde, den Betrag in Schritt 2 erhöhen."
            )
        if (payment.expected_cents or 0) > 0 and payment.paid_cents > payment.expected_cents:
            result["warnings"].append(
                f"{member.display_name()}: {cents_to_euro(payment.paid_cents)} € statt "
                f"{cents_to_euro(payment.expected_cents)} € für {month_label(year, month)} – vielleicht ein Beitrag "
                "für einen anderen Monat (Nachzahlung, Dauerauftrag nach einem Wochenende). Bitte in Schritt 2 prüfen."
            )
        if not payment.paid_date:
            payment.paid_date = entry.booking_date
        # Basistag (Dauerauftragstag) nur vorbelegen, nicht durch das
        # Bankdatum verschieben (Feiertage, Bearbeitungstage der Bank).
        if not member.monthly_value_day:
            member.monthly_value_day = entry.booking_date.day
        note_line = f"Per Kontoauszug übernommen ({cents_to_euro(entry.amount_cents)} €, Kassenbuch #{entry.id})"
        payment.note = f"{payment.note}; {note_line}" if payment.note else note_line

        audit_log(
            "finance",
            "monthly_contribution_imported",
            f"Monatsbeitrag {month_label(year, month)} aus Kontoauszug eingetragen",
            details=(
                f"Mitglied: {member.display_name()}; Betrag: {cents_to_euro(entry.amount_cents)} €; "
                f"Wertstellung: {entry.booking_date}; Kassenbuch #{entry.id}"
            ),
            object_type="MonthlyContributionPayment",
            object_id=payment.id,
        )
        result["linked"] += 1
        result["linked_cents"] += entry.amount_cents
        result["linked_member_ids"].add(member.id)

    # Jedes aktive Mitglied braucht einen Zahlungseintrag, damit der Monat
    # abgeschlossen werden kann (wie beim Speichern in Schritt 2).
    active_members = Member.query.filter_by(active=True).all()
    for member in active_members:
        if member.id not in payments:
            payment = MonthlyContributionPayment(
                batch_id=batch.id, member_id=member.id, expected_cents=default_expected, paid_cents=0,
            )
            db.session.add(payment)
            payments[member.id] = payment
    db.session.flush()

    if finalize:
        if result["warnings"] or result["unmatched"]:
            result["finalize_held"] = contribution_batch_complete(active_members, payments)
        else:
            result["finalized"] = finalize_contribution_batch_if_complete(batch, active_members, payments, user_id)
    return result


def contribution_link_flash_messages(result, year, month, only_entry_ids=None):
    """Meldungen (Text, Kategorie) zum Ergebnis von link_imported_contributions.

    only_entry_ids: Hinweise auf bereits verbuchte Monate / nicht zuordenbare
    Einträge nur für diese Kassenbuch-IDs (z. B. die eben importierten), damit
    alte Fälle nicht bei jedem Import erneut gemeldet werden."""
    label = month_label(year, month)

    def relevant(entries):
        if only_entry_ids is None:
            return entries
        return [entry for entry in entries if entry.id in only_entry_ids]

    messages = []
    if result["linked"]:
        messages.append((
            f"{result['linked']} Mitgliedsbeitrag/-beiträge ({cents_to_euro(result['linked_cents'])} €) "
            f"in „Monatsbeiträge {label}“ als bezahlt eingetragen.",
            "success",
        ))
    if result["finalized"]:
        messages.append((
            f"Alle Monatsbeiträge {label} sind bezahlt – der Monat wurde abgeschlossen"
            + (
                f" ({cents_to_euro(result['finalized']['newly_booked_cents'])} € zusätzlich ins Kassenbuch gebucht)."
                if result["finalized"]["newly_booked_cents"] else
                " (keine zusätzliche Kassenbuchung nötig)."
            ),
            "success",
        ))
    for warning in result.get("warnings", []):
        messages.append((f"Bitte prüfen – {warning}", "warning"))
    if result.get("finalize_held"):
        messages.append((
            f"Alle Monatsbeiträge {label} sind eingetragen, der Monat wurde wegen der Hinweise aber nicht "
            "automatisch abgeschlossen. Bitte in Schritt 2 prüfen und dort speichern.",
            "warning",
        ))
    finalized_before = relevant(result["finalized_before"])
    if finalized_before:
        names = ", ".join(entry.person or "?" for entry in finalized_before)
        messages.append((
            f"Achtung: Die Monatsbeiträge {label} waren bereits verbucht. {len(finalized_before)} übernommene "
            f"Mitgliedsbeitrag-Buchung(en) ({names}) stehen zusätzlich im Kassenbuch – bitte auf Doppelbuchung "
            "prüfen und ggf. im Kassenbuch stornieren.",
            "warning",
        ))
    unmatched = relevant(result["unmatched"])
    if unmatched:
        names = ", ".join(entry.person or "ohne Person" for entry in unmatched)
        messages.append((
            f"{len(unmatched)} Mitgliedsbeitrag-Buchung(en) ({names}) konnten keinem aktiven Mitglied zugeordnet "
            f"werden und zählen nicht in „Monatsbeiträge {label}“.",
            "warning",
        ))
    return messages


def monthly_contribution_rows(year, month, batch=None):
    from app import current_rate_cents

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


def parse_bank_statement_csv(document, max_rows=20):
    from app import active_database_info

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

    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-15"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")

    reader = csv.DictReader(StringIO(text), delimiter=";")
    rows = []
    income_total_cents = 0
    expense_total_cents = 0

    _DATE_COLUMNS = ("Datum", "Buchungstag", "Buchungsdatum", "Valutadatum")
    _TEXT_COLUMNS = ("Buchungstext", "BuchungText", "Text", "Beschreibung", "Verwendungszweck / Notizen")
    _PURPOSE_COLUMNS = ("Verwendungszweck", "VerwendungsZweck", "Zweck", "Buchungstext", "Text")
    _AMOUNT_COLUMNS = ("Betrag", "Umsatz", "Betrag (€)", "Wert")
    _RECIPIENT_COLUMNS = (
        "Beguenstigter/Zahlungspflichtiger",
        "Beguenstigter",
        "Zahlungspflichtiger",
        "Auftraggeber",
        "Empfänger",
        "Zahlungsempfänger",
    )

    def _first_value(row, columns):
        for col in columns:
            val = row.get(col)
            if val is not None and str(val).strip():
                return str(val).strip()
        return ""

    for index, row in enumerate(reader, start=1):
        date_raw = _first_value(row, _DATE_COLUMNS)
        text_raw = _first_value(row, _TEXT_COLUMNS)
        purpose_raw = _first_value(row, _PURPOSE_COLUMNS)
        amount_raw = _first_value(row, _AMOUNT_COLUMNS) or "0"
        recipient_raw = _first_value(row, _RECIPIENT_COLUMNS)

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
                "recipient": recipient_raw,
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
                "recipient": recipient_raw,
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


def parse_bank_statement_row_date(date_raw):
    """Wandelt das Datum einer einzelnen CSV-Zeile robust in ein echtes
    Datum um (deutsche Kontoauszug-Exporte liefern meist TT.MM.JJJJ, manche
    auch ISO). Gibt None zurück, wenn nichts davon passt - die Zeile ist
    dann für die Übernahme gesperrt (siehe build_csv_import_review_rows)."""
    date_raw = (date_raw or "").strip()
    if not date_raw:
        return None
    for fmt in _BANK_STATEMENT_DATE_FORMATS:
        try:
            return datetime.strptime(date_raw, fmt).date()
        except ValueError:
            continue
    return None


MAX_SPLIT_PARTS = 8
OTHER_PERSON_VALUE = "__other__"


def member_person_options():
    """Anzeigenamen aller aktiven Mitglieder für das Personen-Dropdown,
    alphabetisch nach Vorname (dann Nachname) sortiert."""
    members = Member.query.filter_by(active=True).all()
    members.sort(key=lambda m: ((m.first_name or "").lower(), (m.last_name or "").lower()))
    return [m.display_name() for m in members]


def split_amount_evenly(total_cents, parts):
    """Teilt einen Betrag (Cent) möglichst gleichmäßig auf. Ein Rest von
    wenigen Cent geht an die ersten Anteile, die Summe bleibt exakt."""
    parts = max(1, parts)
    base, remainder = divmod(total_cents, parts)
    return [base + (1 if i < remainder else 0) for i in range(parts)]


def collect_booking_parts(form, index, total_cents):
    """Liest die Anteile (Person + Betrag) einer Auszugszeile aus dem Formular.

    Felder je Anteil k (1-basiert): person_<zeile>_<k>, amount_<zeile>_<k>,
    other_<zeile>_<k> (Freitext, wenn "Andere Person" gewählt ist). Das alte
    Feld person_<zeile> gilt weiterhin als Anteil 1.

    Rückgabe: (parts, error). parts = [(person, cents), ...]; bei genau einem
    Anteil immer der volle Betrag. error ist ein Text, wenn die Aufteilung
    nicht stimmt (dann darf die Zeile nicht gebucht werden).
    """
    raw_parts = []
    for k in range(1, MAX_SPLIT_PARTS + 1):
        person = form.get(f"person_{index}_{k}")
        if person is None and k == 1:
            person = form.get(f"person_{index}")
        if person is None:
            continue
        if person == OTHER_PERSON_VALUE:
            person = form.get(f"other_{index}_{k}") or ""
        raw_parts.append((person.strip()[:160], form.get(f"amount_{index}_{k}")))

    if len(raw_parts) <= 1:
        person = raw_parts[0][0] if raw_parts else None
        return [(person, total_cents)], None

    parts = []
    for person, amount_raw in raw_parts:
        try:
            cents = euro_to_cents(amount_raw)
        except ValueError:
            return None, "Betrag eines Anteils ist ungültig"
        if cents <= 0:
            return None, "Jeder Anteil braucht einen Betrag größer 0"
        if not person:
            return None, "Jeder Anteil braucht ein Mitglied bzw. eine Person"
        parts.append((person, cents))

    if sum(cents for _, cents in parts) != total_cents:
        return None, f"Anteile ergeben {cents_to_euro(sum(c for _, c in parts))} € statt {cents_to_euro(total_cents)} €"
    return parts, None


def interest_review_info(document, net_cents, parsed):
    """Hinweistexte für eine Zinsgutschrift in der Prüftabelle:
    (Info zur Aufschlüsselung, Warnung bei bereits gebuchter Zinsbuchung)."""
    period_start = document.document_date
    if not period_start:
        return "", ""
    period_end = date(period_start.year, period_start.month, last_day_of_month(period_start.year, period_start.month))

    if active_interest_booking_for_period(period_start, period_end):
        return "", (
            f"Für {month_label(period_start.year, period_start.month)} ist bereits eine Zinsgutschrift "
            "gebucht – diese Zeile wird nicht noch einmal gebucht."
        )

    breakdown = resolve_interest_breakdown(net_cents, parsed.get("interest_breakdown"))
    taxes = (
        breakdown["capital_gains_tax_cents"] + breakdown["solidarity_tax_cents"] + breakdown["church_tax_cents"]
    )
    if breakdown["source"] == "net_only":
        return "Wird als Zinsbuchung erfasst (nur Netto-Betrag bekannt, Steuern nicht ermittelbar).", ""
    origin = "laut Rechnungsabschluss" if breakdown["source"] == "statement" else "zurückgerechnet"
    return (
        f"Wird als Zinsbuchung erfasst: Brutto {cents_to_euro(breakdown['gross_cents'])} € "
        f"− Steuern {cents_to_euro(taxes)} € = Netto {cents_to_euro(net_cents)} € ({origin}).",
        "",
    )


def build_csv_import_review_rows(document):
    """Baut die Review-Zeilen für den Buchungen-prüfen-Assistenten:
    geparste CSV/PDF-Zeile + Konfidenz-Vorschlag (gelernt/vermutet/kein
    Treffer), weicher Monatsbeitrags-Dubletten-Hinweis und Markierung
    bereits übernommener Zeilen (source_document_id + source_row_index)."""
    if document.category == "Kontoauszug PDF":
        parsed = parse_bank_statement_pdf(document, max_rows=CSV_IMPORT_ROW_LIMIT)
    else:
        parsed = parse_bank_statement_csv(document, max_rows=CSV_IMPORT_ROW_LIMIT)
    if not parsed["ok"]:
        return parsed, []

    # is_void ausschließen: eine stornierte CSV-Buchung darf erneut zur
    # Übernahme angeboten werden - sonst bliebe die Zeile nach einer
    # Korrektur (Stornieren im Kassenbuch) für immer als "bereits
    # übernommen" gesperrt, obwohl sie tatsächlich nicht mehr in der Kasse
    # steht.
    # Eine Auszugszeile kann auf mehrere Mitglieder aufgeteilt und deshalb als
    # mehrere Kassenbuch-Einträge übernommen worden sein.
    existing_entries_by_index = {}
    for entry in (
        CashbookEntry.query.filter_by(source_document_id=document.id)
        .filter(CashbookEntry.is_void == False)  # noqa: E712
        .order_by(CashbookEntry.id)
        .all()
    ):
        if entry.source_row_index is not None:
            existing_entries_by_index.setdefault(entry.source_row_index, []).append(entry)

    review_rows = []
    for row in parsed["rows"]:
        index = row["index"]
        existing_list = existing_entries_by_index.get(index, [])
        existing_entry = existing_list[0] if existing_list else None
        row_date = parse_bank_statement_row_date(row["date"])

        error = row.get("error")
        if not error and row_date is None:
            error = f"Datum konnte nicht gelesen werden: „{row['date']}“"

        entry = {
            "index": index,
            "date_raw": row["date"],
            "date_iso": row_date.isoformat() if row_date else "",
            "text": row["text"],
            "purpose": row["purpose"],
            "recipient": row.get("recipient", ""),
            "amount_cents": row["amount_cents"],
            "amount_euro": row["amount_euro"],
            "type": row["type"],
            "error": error,
            "already_imported": existing_entry is not None,
            "existing_entry": existing_entry,
        }

        if error:
            entry.update({"confidence": "none", "category": "", "person": "", "duplicate_hint": None})
        elif existing_entry:
            entry.update({
                "confidence": "none",
                "category": existing_entry.category,
                "person": " und ".join(e.person for e in existing_list if e.person),
                "duplicate_hint": None,
            })
        else:
            suggestion = suggest_for_row(row["text"], row["purpose"], row["amount_cents"], row.get("recipient", ""))
            entry.update(suggestion)
            entry["duplicate_hint"] = monthly_contribution_duplicate_hint(
                row_date, row["amount_cents"], row["text"], row["purpose"], row.get("recipient", "")
            )
            # Anteile für die Anzeige: je vorgeschlagener Person ein Anteil,
            # Betrag gleichmäßig aufgeteilt (vom Kassierer änderbar).
            suggested = entry.get("suggested_persons") or []
            part_persons = suggested if suggested else [""]
            shares = split_amount_evenly(abs(row["amount_cents"]), len(part_persons))
            entry["parts"] = [
                {"person": person, "amount_euro": cents_to_euro(cents)}
                for person, cents in zip(part_persons, shares)
            ]
            if entry.get("category") == "Zinsen" and row["amount_cents"] > 0:
                entry["interest_info"], entry["interest_hint"] = interest_review_info(
                    document, row["amount_cents"], parsed
                )

        review_rows.append(entry)

    return parsed, review_rows


@app.route("/finance/monthly-bank-closing", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def monthly_bank_closing():
    from app import active_database_info

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


@app.route("/finance/monthly-bank-closing/csv-import", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def monthly_bank_closing_csv_import():
    """Review-Assistent für die CSV-Kontoauszug-Datenübernahme: zeigt die
    geparsten CSV-Zeilen mit vorausgefülltem, farblich markiertem Vorschlag
    (Kategorie/Person je Konfidenz-Stufe) sowie einem weichen
    Monatsbeitrags-Dubletten-Hinweis. Erst nach bewusster Auswahl+Bestätigung
    werden die Zeilen als CashbookEntry/AccountTransaction gebucht - siehe
    build_csv_import_review_rows() und services/csv_import_matching.py."""
    from app import closed_year_block_message

    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    year, month = parse_month_param(request.values.get("month"))
    month_value = f"{year:04d}-{month:02d}"
    period_start = date(year, month, 1)

    csv_document = (
        Document.query
        .filter(Document.category == "Kontoauszug CSV")
        .filter(Document.document_date == period_start)
        .filter(Document.deleted_at.is_(None))
        .order_by(Document.uploaded_at.desc())
        .first()
    )

    pdf_document = (
        Document.query
        .filter(Document.category == "Kontoauszug PDF")
        .filter(Document.document_date == period_start)
        .filter(Document.deleted_at.is_(None))
        .order_by(Document.uploaded_at.desc())
        .first()
    )

    if not csv_document and not pdf_document:
        flash("Für diesen Monat ist keine Kontoauszug-Datei (PDF oder CSV) hochgeladen.", "warning")
        return redirect(url_for("monthly_bank_closing", month=month_value))

    # Präferenz aus Session (welche Dateiart zuletzt gewählt wurde)
    pref_key = f"statement_pref_{year}_{month}"
    pref = session.get(pref_key)

    # Bei POST mit action=select_statement: nur die Auswahl speichern und die
    # Seite neu laden - es wird dabei NICHTS gebucht.
    if request.method == "POST" and request.form.get("action") == "select_statement":
        new_pref = request.form.get("statement_type")
        if new_pref == "csv" and csv_document:
            session[pref_key] = new_pref
            flash("Auswahl gespeichert: CSV-Datei wird verwendet.", "success")
        elif new_pref == "pdf" and pdf_document:
            session[pref_key] = new_pref
            flash("Auswahl gespeichert: PDF-Datei wird verwendet.", "success")
        else:
            flash("Bitte zuerst CSV oder PDF auswählen.", "warning")
        return redirect(url_for("monthly_bank_closing_csv_import", month=month_value))

    # Dokument anhand der Präferenz auswählen
    document = None
    if pref == "pdf" and pdf_document:
        document = pdf_document
    elif pref == "csv" and csv_document:
        document = csv_document
    elif pdf_document:
        document = pdf_document
        session[pref_key] = "pdf"
    elif csv_document:
        document = csv_document
        session[pref_key] = "csv"

    if not document:
        flash("Für diesen Monat ist keine Kontoauszug-Datei (PDF oder CSV) hochgeladen.", "warning")
        return redirect(url_for("monthly_bank_closing", month=month_value))

    if request.method == "POST":
        parsed, review_rows = build_csv_import_review_rows(document)
        if not parsed["ok"]:
            flash(parsed["message"], "danger")
            return redirect(url_for("monthly_bank_closing_csv_import", month=month_value))

        rows_by_index = {row["index"]: row for row in review_rows}
        already_imported_count = sum(1 for row in review_rows if row["already_imported"])

        booked_count = 0
        booked_total_cents = 0
        skipped_closed_year = []
        skipped_invalid = 0
        skipped_split = []
        split_row_count = 0
        skipped_interest = []
        interest_row_count = 0
        new_contribution_entries = []
        # Zinsgutschriften gelten für den Monat des Kontoauszugs (wie die
        # Prüfung "Zinsen" im Monatsabschluss: erster bis letzter Tag).
        period_end_of_month = date(year, month, last_day_of_month(year, month))

        for index_raw in request.form.getlist("take"):
            try:
                index = int(index_raw)
            except ValueError:
                continue

            row = rows_by_index.get(index)
            if not row or row["already_imported"]:
                continue

            if row["error"] or not row["date_iso"]:
                skipped_invalid += 1
                continue

            booking_date = datetime.strptime(row["date_iso"], "%Y-%m-%d").date()

            block_reason = closed_year_block_message(booking_date)
            if block_reason:
                skipped_closed_year.append(f"Zeile {index} ({row['date_raw']})")
                continue

            category = (request.form.get(f"category_{index}") or row["category"] or "").strip()
            if category not in cashbook_category_options():
                category = "Sonstige Einnahme" if row["amount_cents"] >= 0 else "Sonstige Ausgabe"
            total_cents = abs(row["amount_cents"])
            direction = "income" if row["amount_cents"] >= 0 else "expense"
            reason = (row["purpose"] or row["text"] or f"CSV-Import Zeile {index}").strip()[:255]

            # Zinsgutschrift der Bank: als echte Zinsbuchung erfassen (Brutto,
            # Steuern, Netto, Zeitraum), nicht als einfacher Kassenbuch-Eintrag -
            # sonst bleibt "Zinsen" im Monatsabschluss auf "offen".
            if category == "Zinsen" and direction == "income":
                interest_booking, interest_error = book_interest_from_statement(
                    booking_date=booking_date,
                    period_start=period_start,
                    period_end=period_end_of_month,
                    net_cents=total_cents,
                    statement_breakdown=parsed.get("interest_breakdown"),
                    document=document,
                    row_index=index,
                    row_text=row["text"],
                    row_purpose=row["purpose"],
                    user_id=current_user.id,
                )
                if interest_error:
                    skipped_interest.append(f"Zeile {index} ({row['date_raw']}): {interest_error}")
                    continue
                row["already_imported"] = True
                learn_from_confirmation(row["text"], row["purpose"], "Zinsen", "")
                booked_count += 1
                booked_total_cents += total_cents
                interest_row_count += 1
                continue

            # Anteile (Person + Betrag): ein Anteil = volle Buchung, mehrere
            # Anteile = Aufteilung auf mehrere Mitglieder (Summe muss stimmen).
            booking_parts, split_error = collect_booking_parts(request.form, index, total_cents)
            if split_error:
                skipped_split.append(f"Zeile {index} ({row['date_raw']}): {split_error}")
                continue
            if len(booking_parts) == 1 and booking_parts[0][0] is None:
                # Kein Personenfeld übermittelt: Vorschlag der Zeile verwenden.
                booking_parts = [((row["person"] or "").strip()[:160], total_cents)]

            note_lines = []
            if row["text"]:
                note_lines.append(f"Buchungstext: {row['text']}")
            if row["purpose"] and row["purpose"] != reason:
                note_lines.append(f"Verwendungszweck: {row['purpose']}")
            note_lines.append(f"Kontoauszug-Import: {document.original_filename}, Zeile {index}")

            # Zeile sofort als übernommen markieren: verhindert, dass ein
            # doppelt übermittelter "take"-Wert (z. B. durch eine manipulierte
            # Anfrage, da das deaktivierte Checkbox-Attribut clientseitig
            # keinen echten Schutz bietet) dieselbe CSV-Zeile innerhalb
            # desselben Requests mehrfach bucht.
            row["already_imported"] = True

            for part_no, (person, amount_cents) in enumerate(booking_parts, start=1):
                part_note_lines = list(note_lines)
                if len(booking_parts) > 1:
                    part_note_lines.append(
                        f"Aufgeteilt: Anteil {part_no} von {len(booking_parts)} "
                        f"(Gesamtbetrag der Auszugszeile {cents_to_euro(total_cents)} €)"
                    )

                new_entry = CashbookEntry(
                    booking_date=booking_date,
                    direction=direction,
                    account="bank",
                    amount_cents=amount_cents,
                    category=category,
                    person=person,
                    reason=reason,
                    note="\n".join(part_note_lines),
                    created_by_user_id=current_user.id,
                    source_document_id=document.id,
                    source_row_index=index,
                )
                db.session.add(new_entry)
                if category == "Mitgliedsbeitrag" and direction == "income":
                    new_contribution_entries.append(new_entry)

                signed_amount = amount_cents if direction == "income" else -amount_cents
                db.session.add(AccountTransaction(
                    account="bank",
                    category="csv_import",
                    amount_cents=signed_amount,
                    booking_date=booking_date,
                    description=f"Kontoauszug-Import: {category} - {reason}" + (f" ({person})" if person else ""),
                ))

                booked_count += 1
                booked_total_cents += signed_amount

            if len(booking_parts) > 1:
                split_row_count += 1

            learned_person = " und ".join(p for p, _ in booking_parts if p)
            learn_from_confirmation(row["text"], row["purpose"], category, learned_person)

        # Übernommene Mitgliedsbeiträge zusätzlich in "Monatsbeiträge" eintragen
        # (sonst bleibt Schritt 2 auf "0 / n erledigt", und ein späterer Abschluss
        # dort würde die Beiträge ein zweites Mal ins Kassenbuch buchen).
        contribution_result = None
        if new_contribution_entries:
            db.session.flush()
            contribution_result = link_imported_contributions(year, month, current_user.id)

        if booked_count:
            details = (
                f"Datei: {document.original_filename}; Zeitraum {month_label(year, month)}; "
                f"Gesamt: {cents_to_euro(booked_total_cents)} €; "
                f"Bereits zuvor übernommen: {already_imported_count}; "
                f"Übersprungen (Jahr abgeschlossen): {len(skipped_closed_year)}"
            )
            if skipped_closed_year:
                details += " [" + ", ".join(skipped_closed_year) + "]"
            details += f"; Übersprungen (ungültige Zeile): {skipped_invalid}"
            details += f"; Auf mehrere Mitglieder aufgeteilte Zeilen: {split_row_count}"
            details += f"; Als Zinsbuchung erfasste Zeilen: {interest_row_count}"
            if skipped_interest:
                details += f"; Zinsen nicht gebucht: [{'; '.join(skipped_interest)}]"
            if skipped_split:
                details += f"; Übersprungen (Aufteilung stimmt nicht): [{'; '.join(skipped_split)}]"
            if contribution_result:
                details += f"; In Monatsbeiträge eingetragen: {contribution_result['linked']}"
                if contribution_result["finalized"]:
                    details += " (Monat abgeschlossen)"

            audit_log(
                "finance",
                "bank_statement_import_booked",
                f"Kontoauszug-Import gebucht: {booked_count} Buchungen ({month_label(year, month)})",
                details=details,
                object_type="Document",
                object_id=document.id,
                new_value=f"{booked_count} Buchungen, {cents_to_euro(booked_total_cents)} €",
            )

        db.session.commit()

        message_parts = [f"{booked_count} Buchung(en) übernommen."]
        if skipped_closed_year:
            message_parts.append(f"{len(skipped_closed_year)} übersprungen (Jahr bereits abgeschlossen).")
        if skipped_invalid:
            message_parts.append(f"{skipped_invalid} übersprungen (ungültige Zeile).")
        if split_row_count:
            message_parts.append(f"{split_row_count} Zeile(n) auf mehrere Mitglieder aufgeteilt.")
        if interest_row_count:
            message_parts.append(f"{interest_row_count} Zinsgutschrift(en) als Zinsbuchung erfasst.")
        for message in skipped_interest:
            flash(f"Zinsen nicht übernommen – {message}", "danger")
        flash(" ".join(message_parts), "success" if booked_count else "warning")
        if contribution_result:
            new_ids = {entry.id for entry in new_contribution_entries}
            for text, category_name in contribution_link_flash_messages(contribution_result, year, month, new_ids):
                flash(text, category_name)
        # Nicht aufgehende Aufteilungen deutlich melden: die Zeile bleibt offen.
        for message in skipped_split:
            flash(f"Nicht übernommen, Aufteilung stimmt nicht – {message}", "danger")

        return redirect(url_for("monthly_bank_closing_csv_import", month=month_value))

    parsed, review_rows = build_csv_import_review_rows(document)

    return render_template(
        "monthly_bank_closing_csv_import.html",
        year=year,
        month=month,
        month_value=month_value,
        document=document,
        csv_document=csv_document,
        pdf_document=pdf_document,
        statement_pref=pref,
        parsed=parsed,
        review_rows=review_rows,
        categories=cashbook_category_options(),
        member_options=member_person_options(),
        other_person_value=OTHER_PERSON_VALUE,
    )


@app.route("/finance/monthly-contributions", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def monthly_contributions():
    from app import current_rate_cents, closed_year_block_message

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

        # Bereits aus dem Kontoauszug ins Kassenbuch übernommene Mitgliedsbeiträge
        # nachträglich hier eintragen (z. B. Importe vor dieser Verknüpfung).
        if action == "link_imported":
            result = link_imported_contributions(year, month, current_user.id)
            db.session.commit()
            messages = contribution_link_flash_messages(result, year, month)
            if not messages:
                messages = [("Keine offenen Mitgliedsbeiträge aus dem Kontoauszug gefunden.", "info")]
            for text, category_name in messages:
                flash(text, category_name)
            return redirect(url_for("monthly_contributions", month=period_value))

        # Vor jedem Speichern/Bestätigen: noch nicht eingetragene Beiträge aus dem
        # Kontoauszug automatisch eintragen. Sonst würde ein Abschluss hier sie
        # ein zweites Mal ins Kassenbuch buchen. Abgeschlossen wird dabei nichts.
        auto_link = link_imported_contributions(year, month, current_user.id, finalize=False)
        if auto_link["linked"]:
            batch = MonthlyContributionBatch.query.filter_by(year=year, month=month).first()
            db.session.expire(batch, ["payments"])

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

        # Sperre für bereits abgeschlossene Jahre wie beim CSV-Import: hier gibt
        # es kein einzelnes globales Buchungsdatum-Feld (jede Zeile hat ihr
        # eigenes Wertstellungsdatum), daher automatisch zulassen (mit Hinweis
        # für Admins) statt einer Bestätigungs-Checkbox pro Zeile - wie bei
        # anderen Abläufen ohne eigenes Datumsfeld.
        for member in active_members:
            paid_date_raw = request.form.get(f"paid_date_{member.id}", "").strip()
            if not paid_date_raw:
                continue
            try:
                candidate_paid_date = datetime.strptime(paid_date_raw, "%Y-%m-%d").date()
            except ValueError:
                continue
            block_reason = closed_year_block_message(candidate_paid_date, require_admin_confirmation=False)
            if block_reason:
                flash(block_reason, "danger")
                return redirect(url_for("monthly_contributions", month=period_value))

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
        clamped_members = []

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

            # Per Kontoauszug bereits ins Kassenbuch gebuchte Beträge lassen sich
            # hier nicht wegnehmen (sonst stimmen Kassenbuch und Monatsbeiträge
            # nicht mehr überein) - dafür die Buchung im Kassenbuch stornieren.
            imported_cents, imported_first_date = imported_contribution_summary(payment.id)
            if imported_cents and (payment.paid_cents or 0) < imported_cents:
                payment.paid_cents = imported_cents
                payment.paid_date = payment.paid_date or imported_first_date
                clamped_members.append((member.id, member.short_name()))

            processed_payments.append(payment)

        db.session.flush()

        # Monat automatisch abschließen, aber nur wenn wirklich jedes aktive Mitglied
        # einen eigenen bestätigten Zahlungseintrag mit mindestens Sollbetrag hat.
        # Wichtig: Nicht batch.payments verwenden, weil diese Collection während der
        # laufenden Session bei neuen Einträgen unvollständig sein kann.
        # Ein Sollbetrag von 0 € (z.B. Ehrenmitglied) gilt als trivial erfüllt,
        # sonst blockiert ein solches Mitglied den ganzen Monat für immer,
        # weil expected_cents > 0 nie erfüllt wäre.
        # Per Kontoauszug bereits gebuchte Beträge werden dabei nicht noch einmal
        # ins Kassenbuch gebucht (siehe finalize_contribution_batch_if_complete).
        #
        # Nicht abschließen, solange Mitgliedsbeiträge aus dem Kontoauszug im
        # Kassenbuch stehen, die keinem Mitglied zugeordnet werden konnten (sonst
        # würde der Abschluss sie für das eigentliche Mitglied ein zweites Mal
        # buchen) oder das automatische Eintragen eben Hinweise ergeben hat
        # (Kassierer soll sie erst sehen).
        pending_unlinked = unlinked_imported_contribution_entries(year, month)
        hold_finalize = bool(pending_unlinked) or bool(auto_link["warnings"])
        finalized = None
        if not hold_finalize:
            finalized = finalize_contribution_batch_if_complete(
                batch,
                active_members,
                {payment.member_id: payment for payment in processed_payments},
                current_user.id,
            )

        db.session.commit()

        # Nicht zuordenbare Einträge meldet der Hinweis unten (pending_unlinked).
        for text, category_name in contribution_link_flash_messages(dict(auto_link, unmatched=[]), year, month):
            flash(text, category_name)
        if auto_link["warnings"] and not pending_unlinked:
            flash(
                "Monat noch nicht abgeschlossen: Bitte die Hinweise oben prüfen, Beträge ggf. anpassen und "
                "erneut speichern.",
                "warning",
            )
        if pending_unlinked:
            names = ", ".join(entry.person or "ohne Person" for entry in pending_unlinked)
            flash(
                f"Monat nicht abgeschlossen: {len(pending_unlinked)} Mitgliedsbeitrag-Buchung(en) aus dem Kontoauszug "
                f"({names}) sind keinem aktiven Mitglied zugeordnet. Bitte im Kassenbuch stornieren und mit dem "
                "richtigen Mitglied neu aus dem Kontoauszug übernehmen – sonst würde der Beitrag doppelt gebucht.",
                "danger",
            )
        shown_clamped = [name for member_id, name in clamped_members if member_id not in auto_link["linked_member_ids"]]
        if shown_clamped:
            flash(
                "Bereits per Kontoauszug gebuchte Beträge wurden beibehalten ("
                + ", ".join(shown_clamped)
                + "). Zum Korrigieren die Buchung im Kassenbuch stornieren.",
                "warning",
            )

        if finalized:
            message = (
                f"Alle Monatsbeiträge {month_label(year, month)} sind bestätigt und wurden automatisch verbucht: "
                f"{cents_to_euro(finalized['total_cents'])} €."
            )
            if finalized["imported_cents"]:
                message += (
                    f" Davon waren {cents_to_euro(finalized['imported_cents'])} € bereits per Kontoauszug "
                    "gebucht und wurden nicht noch einmal gebucht."
                )
            flash(message, "success")
            return redirect(url_for("monthly_contributions", month=period_value))

        clamped_ids = {member_id for member_id, _ in clamped_members}
        if confirm_member_id:
            flash("Zahlung wurde bestätigt.", "success")
        elif reset_member_id and reset_member_id not in clamped_ids:
            flash("Zahlung wurde zurückgesetzt.", "success")
        elif not reset_member_id:
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

    # Aus dem Kontoauszug übernommene, hier noch nicht eingetragene Beiträge
    # (nur lesen - eingetragen wird erst per Knopf, siehe action "link_imported").
    imported_pending = unlinked_imported_contribution_entries(year, month)

    return render_template(
        "monthly_contributions.html",
        imported_pending_count=len(imported_pending),
        imported_pending_total=cents_to_euro(sum(entry.amount_cents for entry in imported_pending)),
        imported_pending_names=", ".join(entry.person or "ohne Person" for entry in imported_pending),
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


@app.route("/finance/my-monthly-contributions")
@login_required
def my_monthly_contributions():
    member = Member.query.filter_by(user_id=current_user.id).first()
    if not member:
        return render_template("my_monthly_contributions.html", has_member=False)

    payments = (
        MonthlyContributionPayment.query
        .join(MonthlyContributionBatch)
        .filter(MonthlyContributionPayment.member_id == member.id)
        .order_by(MonthlyContributionBatch.year.desc(), MonthlyContributionBatch.month.desc())
        .limit(12)
        .all()
    )

    return render_template(
        "my_monthly_contributions.html",
        has_member=True,
        member=member,
        payments=payments,
    )
