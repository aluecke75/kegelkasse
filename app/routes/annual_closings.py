"""Jahresabschluss: offizieller Jahresabschluss, Schreibsperre für abgeschlossene
Jahre, Jahresbericht (PDF/Export)."""
from datetime import datetime, timedelta

from flask import request, flash, redirect, url_for, render_template
from flask_login import login_required, current_user
from werkzeug.security import check_password_hash

from extensions import app
from auth import role_required
from models import db, Member, BowlingEvent, AnnualClosing, CashAudit, AccountTransaction
from services.money import cents_to_euro
from services.audit import audit_log, annual_closing_snapshot
from services.pdf import _PDF_PAGE_WIDTH, _pdf_text_cmd, _pdf_page_header_cmds, _pdf_footer_cmds, _pdf_assemble, get_logo_pdf_image
from routes.cashbook import account_balance
from routes.penalty_balances import member_penalty_balance
from routes.interest import interest_year_totals


def closing_for_year(year):
    if not year:
        return None
    return AnnualClosing.query.filter_by(year=year).first()


def closed_year_block_message(booking_date, require_admin_confirmation=True, override_field="confirm_closed_year"):
    """Prüft, ob eine Buchung mit diesem Datum in ein bereits abgeschlossenes Jahr fällt.

    Schützt den bereits gespeicherten (ggf. vom Kassenprüfer bestätigten) Jahresabschluss
    davor, durch nachträgliche Buchungen unbemerkt nicht mehr zur echten Kasse zu passen.

    Gibt None zurück, wenn die Buchung zulässig ist, sonst eine Fehlermeldung zum Anzeigen.
    Für Admins gibt es zwei Modi: `require_admin_confirmation=True` verlangt eine bewusste
    Bestätigung im Formular (für frei wählbare Buchungsdaten); bei False wird die Buchung mit
    einem Warnhinweis automatisch zugelassen (für Abläufe ohne eigenes Datumsfeld, z. B. beim
    Abschluss eines bestehenden Kegelabends). Für alle anderen Rollen ist es eine harte Sperre.
    """
    if not booking_date:
        return None

    closing = closing_for_year(booking_date.year)
    if not closing:
        return None

    closed_on = closing.closing_date.strftime("%d.%m.%Y") if closing.closing_date else "-"

    if current_user.role == "admin":
        if not require_admin_confirmation:
            flash(f"Hinweis: Das Jahr {closing.year} ist bereits abgeschlossen (Jahresabschluss vom {closed_on}).", "warning")
            return None
        if request.form.get(override_field) == "1":
            return None
        return (
            f"Das Jahr {closing.year} ist bereits abgeschlossen (Jahresabschluss vom {closed_on}). "
            "Bitte weiter unten bewusst bestätigen, falls trotzdem in diesem Jahr gebucht werden soll."
        )

    return (
        f"Das Jahr {closing.year} ist bereits abgeschlossen (Jahresabschluss vom {closed_on}) und "
        "gegen nachträgliche Buchungen geschützt. Bitte an einen Admin wenden, falls diese Buchung "
        "wirklich in dieses Jahr muss."
    )


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
        "open_event_count": BowlingEvent.query.filter(BowlingEvent.event_date >= start, BowlingEvent.event_date < end, BowlingEvent.status.in_(("open", "settlement", "lane_cost"))).count(),
        "income_cents": income_cents,
        "expense_cents": expense_cents,
        "last_audit": last_audit,
    }


def annual_report_figures(year):
    """Kennzahlen für den Jahresbericht: bevorzugt aus dem gespeicherten Jahresabschluss
    (die offiziellen, eingefrorenen Werte), sonst live aus den aktuellen Buchungen berechnet."""
    closing = closing_for_year(year)
    if closing:
        figures = {
            "source": "closing",
            "closing": closing,
            "cash_balance_cents": closing.cash_balance_cents,
            "bank_balance_cents": closing.bank_balance_cents,
            "total_balance_cents": closing.total_balance_cents,
            "open_penalties_cents": closing.open_penalties_cents,
            "member_credits_cents": closing.member_credits_cents,
            "income_cents": closing.income_cents,
            "expense_cents": closing.expense_cents,
            "event_count": closing.event_count,
            "cancelled_event_count": closing.cancelled_event_count,
            "open_event_count": closing.open_event_count,
            "last_audit": closing.last_cash_audit,
            "closing_date": closing.closing_date,
            "confirmed": bool(closing.confirmed_at),
            "confirmed_by": closing.confirmed_by_user.username if closing.confirmed_by_user else None,
            "confirmed_at": closing.confirmed_at,
        }
    else:
        summary = annual_year_summary(year)
        figures = {
            "source": "live",
            "closing": None,
            "cash_balance_cents": summary["cash_balance_cents"],
            "bank_balance_cents": summary["bank_balance_cents"],
            "total_balance_cents": summary["cash_balance_cents"] + summary["bank_balance_cents"],
            "open_penalties_cents": summary["open_penalties_cents"],
            "member_credits_cents": summary["member_credits_cents"],
            "income_cents": summary["income_cents"],
            "expense_cents": summary["expense_cents"],
            "event_count": summary["event_count"],
            "cancelled_event_count": summary["cancelled_event_count"],
            "open_event_count": summary["open_event_count"],
            "last_audit": summary["last_audit"],
            "closing_date": None,
            "confirmed": False,
            "confirmed_by": None,
            "confirmed_at": None,
        }
    figures["interest"] = interest_year_totals(year)
    return figures


def annual_report_export_row(year):
    figures = annual_report_figures(year)
    return {
        "Jahr": year,
        "Quelle": "Offizieller Jahresabschluss" if figures["source"] == "closing" else "Vorläufig (nicht abgeschlossen)",
        "Barkasse": f"{cents_to_euro(figures['cash_balance_cents'])} €",
        "Bank": f"{cents_to_euro(figures['bank_balance_cents'])} €",
        "Gesamtbestand": f"{cents_to_euro(figures['total_balance_cents'])} €",
        "Einnahmen im Jahr": f"{cents_to_euro(figures['income_cents'])} €",
        "Ausgaben im Jahr": f"{cents_to_euro(figures['expense_cents'])} €",
        "Offene Strafen": f"{cents_to_euro(figures['open_penalties_cents'])} €",
        "Guthaben Mitglieder": f"{cents_to_euro(figures['member_credits_cents'])} €",
        "Brutto-Zinsen": f"{cents_to_euro(figures['interest']['gross_cents'])} €",
        "Netto-Zinsen": f"{cents_to_euro(figures['interest']['net_cents'])} €",
        "Kegelabende abgeschlossen": figures["event_count"],
        "Kegelabende ausgefallen": figures["cancelled_event_count"],
        "Letzte Kassenprüfung": figures["last_audit"].audit_date.strftime("%d.%m.%Y") if figures["last_audit"] and figures["last_audit"].audit_date else "-",
        "Bestätigt": "Ja" if figures["confirmed"] else "Nein",
    }


def build_annual_report_pdf(year):
    """Einseitiger, gut lesbarer Jahresbericht (z. B. für die Mitgliederversammlung)."""
    figures = annual_report_figures(year)
    margin = 42
    page_width = _PDF_PAGE_WIDTH
    now_text = datetime.now().strftime("%d.%m.%Y %H:%M")
    logo_image = get_logo_pdf_image()

    cmds = _pdf_page_header_cmds(1, f"Jahresbericht {year} – Kegelkasse", now_text, margin, logo_image=logo_image)
    y = 700

    def section_title(label, y_pos):
        cmds.append("0.90 0.94 0.91 rg")
        cmds.append(f"{margin} {y_pos - 4} {page_width - 2*margin} 22 re f")
        cmds.append("0 g")
        cmds.append(_pdf_text_cmd(margin + 8, y_pos + 2, label, 12, "F2"))
        return y_pos - 30

    def metric_row(label, value, y_pos):
        cmds.append(_pdf_text_cmd(margin + 8, y_pos, label, 10, "F1"))
        cmds.append(_pdf_text_cmd(page_width - margin - 170, y_pos, value, 11, "F2"))
        return y_pos - 18

    if figures["source"] == "closing":
        status_text = f"Offizieller Jahresabschluss vom {figures['closing_date'].strftime('%d.%m.%Y')}"
        if figures["confirmed"]:
            status_text += f" – bestätigt von {figures['confirmed_by']} am {figures['confirmed_at'].strftime('%d.%m.%Y')}"
        else:
            status_text += " – noch nicht durch Kassenprüfung bestätigt"
    else:
        status_text = "Vorläufige Werte – dieses Jahr wurde noch nicht offiziell abgeschlossen."
    cmds.append(_pdf_text_cmd(margin, y, status_text, 10, "F1"))
    y -= 28

    y = section_title("Kassenbestand", y)
    y = metric_row("Barkasse", f"{cents_to_euro(figures['cash_balance_cents'])} €", y)
    y = metric_row("Bank", f"{cents_to_euro(figures['bank_balance_cents'])} €", y)
    y = metric_row("Gesamtbestand", f"{cents_to_euro(figures['total_balance_cents'])} €", y)
    y -= 10

    y = section_title("Einnahmen & Ausgaben", y)
    y = metric_row("Einnahmen im Jahr", f"{cents_to_euro(figures['income_cents'])} €", y)
    y = metric_row("Ausgaben im Jahr", f"{cents_to_euro(figures['expense_cents'])} €", y)
    y -= 10

    y = section_title("Offene Posten (Stand heute)", y)
    y = metric_row("Offene Strafen", f"{cents_to_euro(figures['open_penalties_cents'])} €", y)
    y = metric_row("Guthaben Mitglieder", f"{cents_to_euro(figures['member_credits_cents'])} €", y)
    y -= 10

    y = section_title("Kegelabende", y)
    y = metric_row("Abgeschlossen", str(figures["event_count"]), y)
    y = metric_row("Ausgefallen", str(figures["cancelled_event_count"]), y)
    y = metric_row("Offen", str(figures["open_event_count"]), y)
    y -= 10

    interest = figures["interest"]
    if interest["count"]:
        y = section_title("Zinsen", y)
        y = metric_row("Brutto-Zinsen", f"{cents_to_euro(interest['gross_cents'])} €", y)
        y = metric_row("Steuern gesamt", f"{cents_to_euro(interest['tax_total_cents'])} €", y)
        y = metric_row("Netto-Zinsen", f"{cents_to_euro(interest['net_cents'])} €", y)
        y -= 10

    y = section_title("Kassenprüfung", y)
    if figures["last_audit"] and figures["last_audit"].audit_date:
        y = metric_row("Letzte Prüfung", figures["last_audit"].audit_date.strftime("%d.%m.%Y"), y)
    else:
        y = metric_row("Letzte Prüfung", "keine", y)

    if figures["closing"] and figures["closing"].note:
        y -= 10
        y = section_title("Notiz zum Abschluss", y)
        cmds.append(_pdf_text_cmd(margin + 8, y, figures["closing"].note[:110], 9, "F1"))
        y -= 18

    cmds.extend(_pdf_footer_cmds(margin))
    return _pdf_assemble([cmds], logo_image=logo_image)


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

    interest_totals = interest_year_totals(selected_year)
    interest_totals_euro = {
        "gross": cents_to_euro(interest_totals["gross_cents"]),
        "tax_total": cents_to_euro(interest_totals["tax_total_cents"]),
        "net": cents_to_euro(interest_totals["net_cents"]),
    }

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
        interest_totals=interest_totals,
        interest_totals_euro=interest_totals_euro,
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
