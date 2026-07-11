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


@app.route("/finance/monthly-contributions", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def monthly_contributions():
    from app import current_rate_cents

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
