"""Strafkonto-Übersicht je Mitglied und Verbuchung von Überweisungen aufs Strafkonto."""
from datetime import datetime

from flask import request, flash, redirect, url_for, render_template
from flask_login import login_required, current_user

from extensions import app
from auth import role_required
from models import db, Member, MemberPenaltyTransaction, AccountTransaction
from services.money import cents_to_euro, form_euro_to_cents
from services.audit import audit_log


def member_penalty_balance(member_id):
    transactions = MemberPenaltyTransaction.query.filter_by(member_id=member_id).all()
    return sum(transaction.amount_cents for transaction in transactions)


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


@app.route("/penalty-balances", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def penalty_balances():
    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    if request.method == "POST":
        from app import closed_year_block_message

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

        block_reason = closed_year_block_message(booking_date)
        if block_reason:
            flash(block_reason, "danger")
            return redirect(url_for("penalty_balances"))

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
