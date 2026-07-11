"""Kassenprüfung: Ist/Soll-Vergleich erfassen und durch Kassenprüfer bestätigen."""
from datetime import datetime

from flask import request, flash, redirect, url_for, render_template
from flask_login import login_required, current_user
from werkzeug.security import check_password_hash

from extensions import app
from auth import role_required
from models import db, CashAudit
from services.money import cents_to_euro, form_euro_to_cents
from services.audit import audit_log, cash_audit_snapshot
from routes.cashbook import account_balance


def cash_audit_export_rows(year=None):
    query = CashAudit.query
    if year:
        query = query.filter(db.extract("year", CashAudit.audit_date) == year)
    audits = query.order_by(CashAudit.audit_date.desc(), CashAudit.created_at.desc()).all()
    rows = []
    for audit in audits:
        rows.append({
            "Prüfdatum": audit.audit_date.strftime("%d.%m.%Y") if audit.audit_date else "",
            "Barkasse laut System": f"{cents_to_euro(audit.expected_cash_cents)} €",
            "Barkasse gezählt": f"{cents_to_euro(audit.counted_cash_cents)} €",
            "Barkasse Differenz": f"{cents_to_euro(audit.difference_cash_cents)} €",
            "Bank laut System": f"{cents_to_euro(audit.expected_bank_cents)} €",
            "Bank laut Auszug": f"{cents_to_euro(audit.statement_bank_cents)} €",
            "Bank Differenz": f"{cents_to_euro(audit.difference_bank_cents)} €",
            "Erfasst von": audit.created_by_user.username if audit.created_by_user else "",
            "Status": "Bestätigt" if audit.confirmed_at else "Offen",
            "Bestätigt von": audit.confirmed_by_user.username if audit.confirmed_by_user else "",
            "Bestätigt am": audit.confirmed_at.strftime("%d.%m.%Y %H:%M") if audit.confirmed_at else "",
            "Notiz": audit.note or "",
            "Prüfernotiz": audit.auditor_note or "",
        })
    return rows


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
