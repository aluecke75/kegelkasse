"""Monatsabschluss: Monatsbeiträge erfassen/verbuchen und der Monatsabschluss-Assistent
(Kontoauszug hochladen, Checkliste Beiträge/Zinsen)."""
import csv
import secrets
from datetime import datetime, date, timedelta
from io import StringIO
from pathlib import Path

from flask import request, flash, redirect, url_for, render_template
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import app
from auth import role_required
from models import db, Member, MonthlyContributionBatch, MonthlyContributionPayment, Document, InterestBooking, CashbookEntry, AccountTransaction
from services.dates import parse_month_param, month_label, first_day_of_month, shift_weekend_to_monday, last_day_of_month
from services.money import cents_to_euro, euro_to_cents
from services.audit import audit_log
from services.csv_import_matching import suggest_for_row, learn_from_confirmation, monthly_contribution_duplicate_hint
from routes.cashbook import cashbook_category_options

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


def build_csv_import_review_rows(document):
    """Baut die Review-Zeilen für den Buchungen-prüfen-Assistenten:
    geparste CSV-Zeile + Konfidenz-Vorschlag (gelernt/vermutet/kein
    Treffer), weicher Monatsbeitrags-Dubletten-Hinweis und Markierung
    bereits übernommener Zeilen (source_document_id + source_row_index)."""
    parsed = parse_bank_statement_csv(document, max_rows=CSV_IMPORT_ROW_LIMIT)
    if not parsed["ok"]:
        return parsed, []

    # is_void ausschließen: eine stornierte CSV-Buchung darf erneut zur
    # Übernahme angeboten werden - sonst bliebe die Zeile nach einer
    # Korrektur (Stornieren im Kassenbuch) für immer als "bereits
    # übernommen" gesperrt, obwohl sie tatsächlich nicht mehr in der Kasse
    # steht.
    existing_by_index = {
        entry.source_row_index: entry
        for entry in CashbookEntry.query.filter_by(source_document_id=document.id).filter(CashbookEntry.is_void == False).all()  # noqa: E712
        if entry.source_row_index is not None
    }

    review_rows = []
    for row in parsed["rows"]:
        index = row["index"]
        existing_entry = existing_by_index.get(index)
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
                "person": existing_entry.person or "",
                "duplicate_hint": None,
            })
        else:
            suggestion = suggest_for_row(row["text"], row["purpose"], row["amount_cents"])
            entry.update(suggestion)
            entry["duplicate_hint"] = monthly_contribution_duplicate_hint(
                row_date, row["amount_cents"], row["text"], row["purpose"]
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

    document = (
        Document.query
        .filter(Document.category == "Kontoauszug CSV")
        .filter(Document.document_date == period_start)
        .filter(Document.deleted_at.is_(None))
        .order_by(Document.uploaded_at.desc())
        .first()
    )

    if not document:
        flash("Für diesen Monat ist keine Kontoauszug-CSV-Datei hochgeladen.", "warning")
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
            person = (request.form.get(f"person_{index}") or row["person"] or "").strip()[:160]

            amount_cents = abs(row["amount_cents"])
            direction = "income" if row["amount_cents"] >= 0 else "expense"
            reason = (row["purpose"] or row["text"] or f"CSV-Import Zeile {index}").strip()[:255]

            note_lines = []
            if row["text"]:
                note_lines.append(f"Buchungstext: {row['text']}")
            if row["purpose"] and row["purpose"] != reason:
                note_lines.append(f"Verwendungszweck: {row['purpose']}")
            note_lines.append(f"CSV-Import: {document.original_filename}, Zeile {index}")

            entry = CashbookEntry(
                booking_date=booking_date,
                direction=direction,
                account="bank",
                amount_cents=amount_cents,
                category=category,
                person=person,
                reason=reason,
                note="\n".join(note_lines),
                created_by_user_id=current_user.id,
                source_document_id=document.id,
                source_row_index=index,
            )
            db.session.add(entry)

            # Zeile sofort als übernommen markieren: verhindert, dass ein
            # doppelt übermittelter "take"-Wert (z. B. durch eine manipulierte
            # Anfrage, da das deaktivierte Checkbox-Attribut clientseitig
            # keinen echten Schutz bietet) dieselbe CSV-Zeile innerhalb
            # desselben Requests mehrfach bucht.
            row["already_imported"] = True

            signed_amount = amount_cents if direction == "income" else -amount_cents
            db.session.add(AccountTransaction(
                account="bank",
                category="csv_import",
                amount_cents=signed_amount,
                booking_date=booking_date,
                description=f"CSV-Import: {category} - {reason}" + (f" ({person})" if person else ""),
            ))

            learn_from_confirmation(row["text"], row["purpose"], category, person)

            booked_count += 1
            booked_total_cents += signed_amount

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

            audit_log(
                "finance",
                "csv_import_booked",
                f"CSV-Import gebucht: {booked_count} Buchungen ({month_label(year, month)})",
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
        flash(" ".join(message_parts), "success" if booked_count else "warning")

        return redirect(url_for("monthly_bank_closing_csv_import", month=month_value))

    parsed, review_rows = build_csv_import_review_rows(document)

    return render_template(
        "monthly_bank_closing_csv_import.html",
        year=year,
        month=month,
        month_value=month_value,
        document=document,
        parsed=parsed,
        review_rows=review_rows,
        categories=cashbook_category_options(),
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
        # Ein Sollbetrag von 0 € (z.B. Ehrenmitglied) gilt als trivial erfüllt,
        # sonst blockiert ein solches Mitglied den ganzen Monat für immer,
        # weil expected_cents > 0 nie erfüllt wäre.
        all_confirmed = bool(active_members) and len(processed_payments) == len(active_members) and all(
            (payment.expected_cents or 0) == 0
            or (
                (payment.paid_cents or 0) >= (payment.expected_cents or 0)
                and payment.paid_date is not None
            )
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
