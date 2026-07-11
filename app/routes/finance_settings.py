"""Finanz-Grundeinstellungen: Beitragssätze/Bahnkosten, Kegelabend-Rhythmus,
Strafarten, Steuereinstellungen für Zinsen."""
from datetime import datetime

from flask import request, flash, redirect, url_for, render_template
from flask_login import login_required

from extensions import app, RATE_TYPES, BASE_RATE_KEYS
from auth import role_required
from models import db, RateSetting, PenaltyType, ParticipantPenalty, AppSetting
from services.settings import setting_value, set_setting_value
from services.money import cents_to_euro, form_euro_to_cents
from services.audit import audit_log, audit_value, audit_diff_lines, penalty_type_audit_snapshot


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
