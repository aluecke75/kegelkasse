"""Zinsmodul: Zinseinstellungen, Zinsgutschriften buchen/stornieren, Steuerabzüge."""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask import request, flash, redirect, url_for, render_template, jsonify
from flask_login import login_required, current_user

from extensions import app
from auth import role_required
from models import db, InterestSetting, InterestBooking, CashbookEntry, AccountTransaction
from services.settings import get_app_setting
from services.money import cents_to_euro, euro_to_cents, form_euro_to_cents, round_tax_cents
from services.audit import audit_log
from routes.cashbook import account_balance, account_balance_as_of


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
    rounding_mode = finance["rounding_mode"]

    gross_interest_cents = Decimal(gross_interest_cents or 0)

    capital_tax = 0
    solidarity_tax = 0
    church_tax = 0

    if finance["capital_tax_enabled"]:
        capital_tax = round_tax_cents(
            gross_interest_cents * Decimal(str(finance["capital_tax_rate"])) / 100,
            rounding_mode,
        )

    if finance["solidarity_enabled"]:
        solidarity_tax = round_tax_cents(
            Decimal(capital_tax) * Decimal(str(finance["solidarity_rate"])) / 100,
            rounding_mode,
        )

    if finance["church_enabled"]:
        church_tax = round_tax_cents(
            Decimal(capital_tax) * Decimal(str(finance["church_rate"])) / 100,
            rounding_mode,
        )

    net_interest = (
        gross_interest_cents
        - capital_tax
        - solidarity_tax
        - church_tax
    )

    return {
        "gross": int(gross_interest_cents),
        "capital_tax": capital_tax,
        "solidarity_tax": solidarity_tax,
        "church_tax": church_tax,
        "net": int(net_interest),
    }


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


def interest_bookings_query(year=None):
    query = InterestBooking.query.filter(InterestBooking.is_cancelled == False)  # noqa: E712
    if year:
        query = query.filter(db.extract("year", InterestBooking.booking_date) == year)
    return query


def interest_year_totals(year=None):
    """Summiert alle nicht stornierten Zinsgutschriften eines Jahres (oder aller Jahre, falls year=None)."""
    bookings = interest_bookings_query(year).all()
    return {
        "count": len(bookings),
        "gross_cents": sum(b.actual_interest_cents for b in bookings),
        "capital_tax_cents": sum(b.capital_gains_tax_cents for b in bookings),
        "solidarity_tax_cents": sum(b.solidarity_tax_cents for b in bookings),
        "church_tax_cents": sum(b.church_tax_cents for b in bookings),
        "tax_total_cents": sum(b.tax_cents for b in bookings),
        "net_cents": sum(b.net_interest_cents for b in bookings),
    }


def interest_year_options():
    years = [
        row[0] for row in
        db.session.query(db.extract("year", InterestBooking.booking_date))
        .filter(InterestBooking.is_cancelled == False)  # noqa: E712
        .distinct().all()
        if row[0] is not None
    ]
    return sorted({int(year) for year in years}, reverse=True)


def interest_year_rows():
    """Zins-Jahresübersicht: eine Zeile pro Jahr mit gebuchten Zinsgutschriften."""
    rows = []
    for year in interest_year_options():
        totals = interest_year_totals(year)
        rows.append({
            "Jahr": year,
            "Anzahl Buchungen": totals["count"],
            "Brutto-Zinsen": f"{cents_to_euro(totals['gross_cents'])} €",
            "Kapitalertragsteuer": f"{cents_to_euro(totals['capital_tax_cents'])} €",
            "Solidaritätszuschlag": f"{cents_to_euro(totals['solidarity_tax_cents'])} €",
            "Kirchensteuer": f"{cents_to_euro(totals['church_tax_cents'])} €",
            "Steuern gesamt": f"{cents_to_euro(totals['tax_total_cents'])} €",
            "Netto-Zinsen": f"{cents_to_euro(totals['net_cents'])} €",
        })
    return rows


def interest_export_rows(year=None):
    bookings = interest_bookings_query(year).order_by(InterestBooking.booking_date.desc()).all()
    rows = []
    for booking in bookings:
        rows.append({
            "Buchungsdatum": booking.booking_date.strftime("%d.%m.%Y") if booking.booking_date else "",
            "Zeitraum": f"{booking.period_start.strftime('%d.%m.%Y')} - {booking.period_end.strftime('%d.%m.%Y')}",
            "Zinssatz": f"{booking.setting.rate_percent()} % p. a." if booking.setting else "-",
            "Bankbestand Grundlage": f"{cents_to_euro(booking.basis_balance_cents)} €",
            "Brutto-Zinsen": f"{cents_to_euro(booking.actual_interest_cents)} €",
            "Kapitalertragsteuer": f"{cents_to_euro(booking.capital_gains_tax_cents)} €",
            "Solidaritätszuschlag": f"{cents_to_euro(booking.solidarity_tax_cents)} €",
            "Kirchensteuer": f"{cents_to_euro(booking.church_tax_cents)} €",
            "Netto-Zinsen": f"{cents_to_euro(booking.net_interest_cents)} €",
            "Notiz": booking.note or "",
        })
    return rows


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
    from app import closed_year_block_message

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

            block_reason = closed_year_block_message(booking_date)
            if block_reason:
                flash(block_reason, "danger")
                return redirect(url_for("interest_module"))

            # Überlappung statt nur exakt gleicher Zeitraum: zwei Standard-
            # Intervall-Bedingungen (start <= anderes Ende UND ende >= anderes
            # Start) erkennen auch teilweise überschneidende Zinsperioden
            # (z. B. Jan-Mär und Feb-Apr), nicht nur exakte Duplikate.
            existing_interest_booking = (
                InterestBooking.query
                .filter(InterestBooking.period_start <= period_end)
                .filter(InterestBooking.period_end >= period_start)
                .filter(InterestBooking.is_cancelled == False)
                .first()
            )

            if existing_interest_booking:
                flash(
                    "Für diesen Zinszeitraum wurde bereits eine aktive Zinsgutschrift gebucht. Eine Doppelbuchung ist nicht erlaubt.",
                    "warning"
                )
                return redirect(url_for("interest_module"))

            # Historischer Stand zum Ende des Zinszeitraums statt des heutigen
            # Live-Saldos - sonst wird bei späterer Buchung mit einem inzwischen
            # gestiegenen/gesunkenen Kontostand statt dem tatsächlichen
            # Periodenstand gerechnet.
            basis_balance_cents = account_balance_as_of("bank", period_end)
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

    bank_balance_cents = account_balance_as_of("bank", default_end)
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
