"""Kassenbuch: Einnahmen/Ausgaben/Umbuchungen, Startbestände, Storno."""
from datetime import datetime

from flask import request, flash, redirect, url_for, render_template
from flask_login import login_required, current_user
from sqlalchemy import or_

from extensions import app
from auth import role_required
from models import db, CashbookEntry, AccountTransaction, User
from services.money import cents_to_euro, euro_to_cents, form_euro_to_cents
from services.audit import audit_log


def account_balance(account):
    transactions = AccountTransaction.query.filter_by(account=account).all()
    return sum(transaction.amount_cents for transaction in transactions)


def account_balance_as_of(account, date):
    """Historischer Kontostand: Summe aller Buchungen bis einschließlich diesem Datum."""
    total = db.session.query(db.func.sum(AccountTransaction.amount_cents)).filter(
        AccountTransaction.account == account,
        AccountTransaction.booking_date <= date,
    ).scalar()
    return total or 0


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

        audit_log(
            "finance",
            "opening_balances_saved",
            "Startbestände gespeichert",
            details=f"Barkasse: {cents_to_euro(cash_cents)} €; Bank: {cents_to_euro(bank_cents)} €; Datum: {booking_date}; Beschreibung: {description or '-'}",
        )
        db.session.commit()

        flash("Startbestände wurden gespeichert.", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "opening_balances.html",
        today=datetime.today().date().isoformat(),
        cash_balance=cents_to_euro(account_balance("cash")),
        bank_balance=cents_to_euro(account_balance("bank")),
    )


@app.route("/cashbook", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def cashbook():
    from app import closed_year_block_message

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

        block_reason = closed_year_block_message(booking_date)
        if block_reason:
            flash(block_reason, "danger")
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
