"""Statistiken/Berichte, Auswertungs-Exporte und das Revisionsprotokoll."""
from datetime import datetime, timedelta

from flask import request, flash, redirect, url_for, render_template, Response
from flask_login import login_required, current_user
from sqlalchemy import or_

from extensions import app
from auth import role_required
from models import db, Member, BowlingEvent, EventParticipant, ParticipantPenalty, PenaltyType, MemberPenaltyTransaction, AccountTransaction, CashbookEntry, AnnualClosing, AuditLog
from services.dates import _month_label, _last_n_months
from services.money import cents_to_euro
from services.audit import audit_log, audit_object_label, annual_closing_snapshot, cleanup_old_audit_log_entries, count_old_audit_log_entries, RETENTION_ELIGIBLE_CATEGORIES
from services.settings import setting_value, set_setting_value, safe_int_setting
from services.export import export_filename, export_response, export_headers
from services.pdf import _PDF_PAGE_WIDTH, _pdf_text_cmd, _pdf_page_header_cmds, _pdf_footer_cmds, _pdf_assemble, get_logo_pdf_image
from routes.cashbook import account_balance, cashbook_export_rows
from routes.penalty_balances import member_penalty_balance, penalty_balance_export_rows
from routes.cash_audits import cash_audit_export_rows
from routes.interest import interest_year_totals, interest_export_rows


def report_year_options():
    years = [row[0] for row in db.session.query(db.extract("year", BowlingEvent.event_date)).distinct().all() if row[0] is not None]
    years = sorted({int(year) for year in years}, reverse=True)
    return years


def report_event_query(selected_year):
    query = BowlingEvent.query.filter(BowlingEvent.status == "closed")
    if selected_year:
        query = query.filter(db.extract("year", BowlingEvent.event_date) == selected_year)
    return query


def report_member_name(member):
    return member.nickname or member.first_name or member.display_name()


def build_count_stat_rows(selected_year, penalty_key):
    query = (
        db.session.query(
            Member.id,
            Member.first_name,
            Member.last_name,
            Member.nickname,
            db.func.coalesce(db.func.sum(ParticipantPenalty.quantity), 0).label("total"),
        )
        .join(EventParticipant, EventParticipant.member_id == Member.id)
        .join(BowlingEvent, BowlingEvent.id == EventParticipant.event_id)
        .join(ParticipantPenalty, ParticipantPenalty.participant_id == EventParticipant.id)
        .join(PenaltyType, PenaltyType.id == ParticipantPenalty.penalty_type_id)
        .filter(BowlingEvent.status == "closed")
        .filter(PenaltyType.key == penalty_key)
    )

    if selected_year:
        query = query.filter(db.extract("year", BowlingEvent.event_date) == selected_year)

    results = (
        query.group_by(Member.id)
        .order_by(db.desc("total"), Member.first_name, Member.last_name)
        .all()
    )

    return [
        {
            "member_id": row.id,
            "name": row.nickname or row.first_name or f"{row.first_name} {row.last_name}",
            "total": int(row.total or 0),
        }
        for row in results
        if int(row.total or 0) > 0
    ]


def build_absence_stat_rows(selected_year):
    query = (
        db.session.query(
            Member.id,
            Member.first_name,
            Member.last_name,
            Member.nickname,
            db.func.sum(db.case((EventParticipant.status == "excused", 1), else_=0)).label("excused"),
            db.func.sum(db.case((EventParticipant.status == "unexcused", 1), else_=0)).label("unexcused"),
        )
        .join(EventParticipant, EventParticipant.member_id == Member.id)
        .join(BowlingEvent, BowlingEvent.id == EventParticipant.event_id)
        .filter(BowlingEvent.status == "closed")
    )

    if selected_year:
        query = query.filter(db.extract("year", BowlingEvent.event_date) == selected_year)

    results = query.group_by(Member.id).all()
    rows = []

    for row in results:
        excused = int(row.excused or 0)
        unexcused = int(row.unexcused or 0)
        total = excused + unexcused
        if total <= 0:
            continue
        rows.append({
            "member_id": row.id,
            "name": row.nickname or row.first_name or f"{row.first_name} {row.last_name}",
            "excused": excused,
            "unexcused": unexcused,
            "total": total,
        })

    rows.sort(key=lambda item: (-item["total"], item["name"].casefold()))
    return rows


def build_penalty_money_rows(selected_year):
    query = (
        db.session.query(
            Member.id,
            Member.first_name,
            Member.last_name,
            Member.nickname,
            db.func.coalesce(db.func.sum(MemberPenaltyTransaction.amount_cents), 0).label("total"),
        )
        .join(MemberPenaltyTransaction, MemberPenaltyTransaction.member_id == Member.id)
        .filter(MemberPenaltyTransaction.category == "event_penalty")
    )

    if selected_year:
        query = query.filter(db.extract("year", MemberPenaltyTransaction.booking_date) == selected_year)

    results = query.group_by(Member.id).order_by(db.desc("total"), Member.first_name, Member.last_name).all()
    return [
        {
            "name": row.nickname or row.first_name or f"{row.first_name} {row.last_name}",
            "total_cents": int(row.total or 0),
            "total_euro": cents_to_euro(row.total or 0),
        }
        for row in results
        if int(row.total or 0) > 0
    ]


def member_payment_summary(selected_year=None):
    """Summiert echte Einzahlungen je Mitglied für Admin/Kassierer.

    Gezählt werden Barzahlungen am Kegelabend und spätere Überweisungen.
    Negative Beträge in MemberPenaltyTransaction sind Zahlungen/Guthabenabbau.
    """
    query = (
        db.session.query(
            Member.id,
            Member.first_name,
            Member.last_name,
            Member.nickname,
            db.func.sum(MemberPenaltyTransaction.amount_cents).label("total_cents"),
        )
        .join(MemberPenaltyTransaction, MemberPenaltyTransaction.member_id == Member.id)
        .filter(MemberPenaltyTransaction.category.in_(["cash_payment", "bank_transfer"]))
    )

    if selected_year:
        query = query.filter(db.extract("year", MemberPenaltyTransaction.booking_date) == selected_year)

    rows = (
        query.group_by(Member.id)
        .order_by(Member.first_name.asc(), Member.last_name.asc())
        .all()
    )

    result = []
    total_paid = 0
    for row in rows:
        paid_cents = abs(row.total_cents or 0)
        total_paid += paid_cents
        name = row.nickname or f"{row.first_name} {row.last_name}"
        result.append({
            "name": name,
            "paid_cents": paid_cents,
            "paid_euro": cents_to_euro(paid_cents),
        })

    average_cents = int(round(total_paid / len(result))) if result else 0

    return result, cents_to_euro(total_paid), cents_to_euro(average_cents)


def event_year_filter(query, selected_year):
    if selected_year:
        return query.filter(db.extract("year", BowlingEvent.event_date) == selected_year)
    return query


def member_active_years(member_id):
    years = (
        db.session.query(db.extract("year", BowlingEvent.event_date))
        .join(EventParticipant, EventParticipant.event_id == BowlingEvent.id)
        .filter(EventParticipant.member_id == member_id)
        .filter(BowlingEvent.status == "closed")
        .distinct()
        .all()
    )
    return sorted({int(row[0]) for row in years if row[0] is not None})


def build_player_overview_rows(selected_year=None, member_id=None):
    members_query = Member.query.order_by(Member.first_name.asc(), Member.last_name.asc())
    if member_id:
        members_query = members_query.filter_by(id=member_id)
    members = members_query.all()
    rows = []

    for member in members:
        participant_query = (
            EventParticipant.query
            .join(BowlingEvent, BowlingEvent.id == EventParticipant.event_id)
            .filter(EventParticipant.member_id == member.id)
            .filter(BowlingEvent.status == "closed")
        )
        participant_query = event_year_filter(participant_query, selected_year)
        participants = participant_query.all()

        attended = sum(1 for p in participants if p.status == "present")
        excused = sum(1 for p in participants if p.status == "excused")
        unexcused = sum(1 for p in participants if p.status == "unexcused")

        penalty_query = (
            MemberPenaltyTransaction.query
            .filter(MemberPenaltyTransaction.member_id == member.id)
            .filter(MemberPenaltyTransaction.category == "event_penalty")
        )
        if selected_year:
            penalty_query = penalty_query.filter(db.extract("year", MemberPenaltyTransaction.booking_date) == selected_year)
        penalty_transactions = penalty_query.all()
        penalty_total_cents = sum(t.amount_cents or 0 for t in penalty_transactions)

        payment_query = (
            MemberPenaltyTransaction.query
            .filter(MemberPenaltyTransaction.member_id == member.id)
            .filter(MemberPenaltyTransaction.category.in_(["cash_payment", "bank_transfer"]))
        )
        if selected_year:
            payment_query = payment_query.filter(db.extract("year", MemberPenaltyTransaction.booking_date) == selected_year)
        paid_cents = abs(sum(t.amount_cents or 0 for t in payment_query.all()))

        highest_single = 0
        for participant in participants:
            event_amount = sum((tx.amount_cents or 0) for tx in penalty_transactions if tx.participant_id == participant.id)
            highest_single = max(highest_single, event_amount)

        current_balance = member_penalty_balance(member.id)
        open_cents = current_balance if current_balance > 0 else 0
        credit_cents = abs(current_balance) if current_balance < 0 else 0

        rows.append({
            "name": report_member_name(member),
            "attended": attended,
            "excused": excused,
            "unexcused": unexcused,
            "absences": excused + unexcused,
            "penalty_total_cents": penalty_total_cents,
            "penalty_total_euro": cents_to_euro(penalty_total_cents),
            "highest_single_cents": highest_single,
            "highest_single_euro": cents_to_euro(highest_single),
            "paid_cents": paid_cents,
            "paid_euro": cents_to_euro(paid_cents),
            "open_cents": open_cents,
            "open_euro": cents_to_euro(open_cents),
            "credit_cents": credit_cents,
            "credit_euro": cents_to_euro(credit_cents),
        })

    return rows


def build_event_overview_rows(selected_year=None):
    events = report_event_query(selected_year).order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc()).all()
    rows = []
    for event in events:
        participants = EventParticipant.query.filter_by(event_id=event.id).all()
        present_count = sum(1 for p in participants if p.status in ["present", "guest"])
        absence_count = sum(1 for p in participants if p.status in ["excused", "unexcused"])
        member_count = sum(1 for p in participants if p.member_id is not None)
        guest_count = sum(1 for p in participants if p.status == "guest" or p.member_id is None)
        penalty_sum = sum(
            tx.amount_cents or 0
            for tx in MemberPenaltyTransaction.query
                .filter(MemberPenaltyTransaction.event_id == event.id)
                .filter(MemberPenaltyTransaction.category == "event_penalty")
                .all()
        )
        rows.append({
            "date": event.event_date,
            "present_count": present_count,
            "absence_count": absence_count,
            "member_count": member_count,
            "guest_count": guest_count,
            "penalty_sum_cents": penalty_sum,
            "penalty_sum_euro": cents_to_euro(penalty_sum),
            "lane_cost_cents": event.lane_cost_cents or 0,
            "lane_cost_euro": cents_to_euro(event.lane_cost_cents or 0),
        })
    return rows


def build_report_totals(player_rows, event_rows, selected_year=None):
    total_penalties = sum(row["penalty_total_cents"] for row in player_rows)
    total_paid = sum(row["paid_cents"] for row in player_rows)
    total_open = sum(row["open_cents"] for row in player_rows)
    total_credit = sum(row["credit_cents"] for row in player_rows)
    total_lane_costs = sum(row["lane_cost_cents"] for row in event_rows)
    average_participants = 0
    if event_rows:
        average_participants = round(sum(row["present_count"] for row in event_rows) / len(event_rows), 1)

    cashbook_query = CashbookEntry.query.filter(CashbookEntry.is_void == False)  # noqa: E712
    if selected_year:
        cashbook_query = cashbook_query.filter(db.extract("year", CashbookEntry.booking_date) == selected_year)
    entries = cashbook_query.all()
    income = sum(e.amount_cents or 0 for e in entries if e.direction == "income")
    expense = sum(e.amount_cents or 0 for e in entries if e.direction == "expense")

    return {
        "total_penalties_euro": cents_to_euro(total_penalties),
        "total_paid_euro": cents_to_euro(total_paid),
        "total_open_euro": cents_to_euro(total_open),
        "total_credit_euro": cents_to_euro(total_credit),
        "total_lane_costs_euro": cents_to_euro(total_lane_costs),
        "average_participants": str(average_participants).replace(".", ","),
        "cashbook_income_euro": cents_to_euro(income),
        "cashbook_expense_euro": cents_to_euro(expense),
    }


def top_rows(rows, key, reverse=True, limit=10):
    filtered = [row for row in rows if row.get(key, 0)]
    return sorted(filtered, key=lambda row: (row.get(key, 0), row.get("name", "")), reverse=reverse)[:limit]


def report_penalty_month_series(months_back=15):
    months = _last_n_months(months_back)
    transactions = MemberPenaltyTransaction.query.filter_by(category="event_penalty").all()
    totals_by_month = {}
    for tx in transactions:
        if not tx.booking_date:
            continue
        key = (tx.booking_date.year, tx.booking_date.month)
        totals_by_month[key] = totals_by_month.get(key, 0) + (tx.amount_cents or 0)

    points = []
    for year, month in months:
        cents = totals_by_month.get((year, month), 0)
        points.append({
            "x": _month_label(year, month),
            "y": round(cents / 100, 2),
            "label": _month_label(year, month),
        })
    return points


def report_attendance_month_series(months_back=15):
    months = _last_n_months(months_back)
    participants = (
        db.session.query(EventParticipant.status, BowlingEvent.event_date)
        .join(BowlingEvent, BowlingEvent.id == EventParticipant.event_id)
        .filter(BowlingEvent.status == "closed")
        .all()
    )
    counts_by_month = {}
    for status, event_date in participants:
        if not event_date:
            continue
        key = (event_date.year, event_date.month)
        bucket = counts_by_month.setdefault(key, {"present": 0, "excused": 0, "unexcused": 0})
        if status in bucket:
            bucket[status] += 1

    labels = [_month_label(year, month) for year, month in months]
    series_present = []
    series_excused = []
    series_unexcused = []
    for year, month in months:
        bucket = counts_by_month.get((year, month), {"present": 0, "excused": 0, "unexcused": 0})
        label = _month_label(year, month)
        series_present.append({"x": label, "y": bucket["present"], "label": label})
        series_excused.append({"x": label, "y": bucket["excused"], "label": label})
        series_unexcused.append({"x": label, "y": bucket["unexcused"], "label": label})
    return labels, series_present, series_excused, series_unexcused


def report_year_comparison_rows():
    years = sorted({t.booking_date.year for t in AccountTransaction.query.all() if t.booking_date})
    rows = []
    for year in years:
        start = datetime(year, 1, 1).date()
        end = datetime(year + 1, 1, 1).date()
        transactions = AccountTransaction.query.filter(
            AccountTransaction.booking_date >= start,
            AccountTransaction.booking_date < end,
        ).all()
        income_cents = sum(t.amount_cents for t in transactions if (t.amount_cents or 0) > 0)
        expense_cents = abs(sum(t.amount_cents for t in transactions if (t.amount_cents or 0) < 0))
        rows.append({"year": year, "income_cents": income_cents, "expense_cents": expense_cents})
    return rows


def report_cash_balance_history_rows():
    """Barkasse/Bank zum Jahresende, aus gespeicherten Jahresabschlüssen. Zusätzlich der
    heutige Live-Stand, falls das laufende Jahr noch nicht abgeschlossen ist."""
    closings = AnnualClosing.query.order_by(AnnualClosing.year.asc()).all()
    rows = [
        {
            "label": str(closing.year),
            "cash_cents": closing.cash_balance_cents,
            "bank_cents": closing.bank_balance_cents,
        }
        for closing in closings
    ]

    current_year = datetime.today().year
    if not any(closing.year == current_year for closing in closings):
        rows.append({
            "label": f"{current_year} (heute)",
            "cash_cents": account_balance("cash"),
            "bank_cents": account_balance("bank"),
        })
    return rows


def members_export_rows(scope="active"):
    members = Member.query.order_by(Member.first_name.asc(), Member.last_name.asc()).all()
    rows = []
    for member in members:
        balance = member_penalty_balance(member.id)
        if scope == "active" and not member.active:
            continue
        if scope == "open" and balance <= 0:
            continue
        if scope == "credit" and balance >= 0:
            continue
        rows.append({
            "Name": member.display_name(),
            "Vorname": member.first_name or "",
            "Nachname": member.last_name or "",
            "Spitzname": member.nickname or "",
            "E-Mail": member.email or (member.user.email if member.user else ""),
            "Benutzername": member.user.username if member.user else "",
            "Rolle": member.user.role_label() if member.user else "Mitglied ohne Login",
            "Aktiv": "Ja" if member.active else "Nein",
            "Dauerauftragstag": member.monthly_value_day or "",
            "Offene Strafen": f"{cents_to_euro(balance)} €" if balance > 0 else "0,00 €",
            "Guthaben": f"{cents_to_euro(abs(balance))} €" if balance < 0 else "0,00 €",
        })
    return rows


def annual_closing_export_rows(year=None):
    query = AnnualClosing.query
    if year:
        query = query.filter(AnnualClosing.year == year)
    closings = query.order_by(AnnualClosing.year.desc()).all()
    rows = []
    for closing in closings:
        snapshot = annual_closing_snapshot(closing)
        rows.append(snapshot)
    return rows


def statistics_export_rows(year=None):
    player_rows = build_player_overview_rows(year)
    rows = []
    for row in player_rows:
        rows.append({
            "Mitglied": row["name"],
            "Anwesend": row["attended"],
            "Fehlt entschuldigt": row["excused"],
            "Fehlt unentschuldigt": row["unexcused"],
            "Strafen gesamt": f"{row['penalty_total_euro']} €",
            "Höchste Einzelstrafe": f"{row['highest_single_euro']} €",
            "Eingezahlt": f"{row['paid_euro']} €",
            "Offen": f"{row['open_euro']} €",
            "Guthaben": f"{row['credit_euro']} €",
        })
    return rows


def export_filter_year():
    raw = request.args.get("year", "all")
    if raw and raw != "all":
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def export_year_options():
    years = set(report_year_options())
    for closing in AnnualClosing.query.all():
        if closing.year:
            years.add(closing.year)
    for entry in CashbookEntry.query.all():
        if entry.booking_date:
            years.add(entry.booking_date.year)
    return sorted(years, reverse=True)


@app.route("/reports")
@login_required
def reports():
    year_raw = request.args.get("year", "all")
    selected_year = None
    if year_raw != "all":
        try:
            selected_year = int(year_raw)
        except ValueError:
            selected_year = None

    closed_events = report_event_query(selected_year).count()
    cancelled_query = BowlingEvent.query.filter(BowlingEvent.status == "cancelled")
    if selected_year:
        cancelled_query = cancelled_query.filter(db.extract("year", BowlingEvent.event_date) == selected_year)

    pump_rows = build_count_stat_rows(selected_year, "penalty_pump")
    wreath_rows = build_count_stat_rows(selected_year, "penalty_wreath")
    absence_rows = build_absence_stat_rows(selected_year)
    money_rows = build_penalty_money_rows(selected_year)
    payment_rows, payment_total_euro, payment_average_euro = member_payment_summary(selected_year)
    player_rows = build_player_overview_rows(selected_year)
    event_rows = build_event_overview_rows(selected_year)
    report_totals = build_report_totals(player_rows, event_rows, selected_year)

    highest_event_rows = sorted(event_rows, key=lambda row: row["penalty_sum_cents"], reverse=True)[:10]
    lowest_event_rows = sorted([row for row in event_rows if row["penalty_sum_cents"] > 0], key=lambda row: row["penalty_sum_cents"])[:10]

    interest_totals = interest_year_totals(selected_year)
    interest_totals_euro = {
        "gross": cents_to_euro(interest_totals["gross_cents"]),
        "capital_tax": cents_to_euro(interest_totals["capital_tax_cents"]),
        "solidarity_tax": cents_to_euro(interest_totals["solidarity_tax_cents"]),
        "church_tax": cents_to_euro(interest_totals["church_tax_cents"]),
        "tax_total": cents_to_euro(interest_totals["tax_total_cents"]),
        "net": cents_to_euro(interest_totals["net_cents"]),
    }

    penalty_month_points = report_penalty_month_series()
    attendance_labels, attendance_present, attendance_excused, attendance_unexcused = report_attendance_month_series()
    year_comparison_rows = report_year_comparison_rows()
    cash_balance_history_rows = report_cash_balance_history_rows()

    chart_data = {
        "penaltyTrend": {
            "series": [{"name": "Strafgeld", "points": penalty_month_points}],
        },
        "attendanceTrend": {
            "labels": attendance_labels,
            "series": [
                {"name": "Anwesend", "points": attendance_present},
                {"name": "Entschuldigt", "points": attendance_excused},
                {"name": "Unentschuldigt", "points": attendance_unexcused},
            ],
        },
        "yearComparison": {
            "groups": [str(row["year"]) for row in year_comparison_rows],
            "series": [
                {"name": "Einnahmen", "values": [round(row["income_cents"] / 100, 2) for row in year_comparison_rows]},
                {"name": "Ausgaben", "values": [round(row["expense_cents"] / 100, 2) for row in year_comparison_rows]},
            ],
        },
        "cashBalanceHistory": {
            "xLabels": [row["label"] for row in cash_balance_history_rows],
            "series": [
                {"name": "Barkasse", "points": [{"x": row["label"], "y": round(row["cash_cents"] / 100, 2), "label": row["label"]} for row in cash_balance_history_rows]},
                {"name": "Bank", "points": [{"x": row["label"], "y": round(row["bank_cents"] / 100, 2), "label": row["label"]} for row in cash_balance_history_rows]},
            ],
        },
    }

    return render_template(
        "reports.html",
        chart_data=chart_data,
        year_comparison_rows=year_comparison_rows,
        cash_balance_history_rows=cash_balance_history_rows,
        penalty_month_points=penalty_month_points,
        attendance_trend_rows=[
            {"label": label, "present": p["y"], "excused": e["y"], "unexcused": u["y"]}
            for label, p, e, u in zip(attendance_labels, attendance_present, attendance_excused, attendance_unexcused)
        ],
        years=report_year_options(),
        selected_year=selected_year,
        selected_year_value=str(selected_year) if selected_year else "all",
        closed_events=closed_events,
        cancelled_events=cancelled_query.count(),
        pump_rows=pump_rows,
        wreath_rows=wreath_rows,
        absence_rows=absence_rows,
        money_rows=money_rows,
        payment_rows=payment_rows,
        payment_total_euro=payment_total_euro,
        payment_average_euro=payment_average_euro,
        player_rows=player_rows,
        event_rows=event_rows,
        report_totals=report_totals,
        most_penalties_rows=top_rows(player_rows, "penalty_total_cents"),
        least_penalties_rows=top_rows(player_rows, "penalty_total_cents", reverse=False),
        highest_single_rows=top_rows(player_rows, "highest_single_cents"),
        most_attended_rows=top_rows(player_rows, "attended"),
        most_open_rows=top_rows(player_rows, "open_cents"),
        most_credit_rows=top_rows(player_rows, "credit_cents"),
        highest_event_rows=highest_event_rows,
        lowest_event_rows=lowest_event_rows,
        interest_totals=interest_totals,
        interest_totals_euro=interest_totals_euro,
        interest_bookings_count=interest_totals["count"],
    )


@app.route("/exports")
@login_required
@role_required("admin", "cashier", "auditor")
def exports_page():
    from routes.events import event_export_candidates

    return render_template(
        "exports.html",
        years=export_year_options(),
        current_year=datetime.now().year,
        event_candidates=event_export_candidates(),
    )


@app.route("/exports/download")
@login_required
@role_required("admin", "cashier", "auditor")
def exports_download():
    from app import build_annual_report_pdf, annual_report_export_row
    from routes.events import build_event_protocol_pdf

    export_type = request.args.get("type", "cashbook")
    fmt = request.args.get("format", "excel")
    year = export_filter_year()
    account = request.args.get("account", "all")
    member_scope = request.args.get("member_scope", "active")

    if export_type == "event_protocol":
        try:
            event_id = int(request.args.get("event_id", ""))
        except (TypeError, ValueError):
            event_id = None
        event = BowlingEvent.query.filter_by(id=event_id, status="closed").first() if event_id else None
        if not event:
            flash("Bitte einen abgeschlossenen Kegelabend auswählen.", "danger")
            return redirect(url_for("exports_page"))

        title = f"Kegelabend-Protokoll {event.event_date.strftime('%d.%m.%Y')}"
        audit_log(
            "system",
            "export_created",
            f"Export erstellt: {title}",
            details=f"Bereich: {title}\nFormat: pdf\nKegelabend: #{event.id}",
            object_type="Export",
            new_value=title,
        )
        db.session.commit()
        return Response(
            build_event_protocol_pdf(event),
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={export_filename(f'kegelabend_protokoll_{event.event_date.isoformat()}', 'pdf')}"},
        )

    if export_type == "annual_report":
        report_year = year or (datetime.now().year - 1)
        title = f"Jahresbericht {report_year}"
        audit_log(
            "system",
            "export_created",
            f"Export erstellt: {title}",
            details=f"Bereich: {title}\nFormat: {fmt}\nJahr: {report_year}",
            object_type="Export",
            new_value=title,
        )
        db.session.commit()
        if fmt == "pdf":
            return Response(
                build_annual_report_pdf(report_year),
                mimetype="application/pdf",
                headers={"Content-Disposition": f"attachment; filename={export_filename(f'jahresbericht_{report_year}', 'pdf')}"},
            )
        row = annual_report_export_row(report_year)
        return export_response([row], list(row.keys()), export_type, fmt, title)

    if export_type == "cashbook":
        rows = cashbook_export_rows(year, account)
        title = "Kassenbuch Export"
    elif export_type == "members":
        rows = members_export_rows(member_scope)
        title = "Mitglieder Export"
    elif export_type == "penalty_balances":
        rows = penalty_balance_export_rows()
        title = "Strafkonten Export"
    elif export_type == "annual_closing":
        rows = annual_closing_export_rows(year)
        title = "Jahresabschluss Export"
    elif export_type == "statistics":
        rows = statistics_export_rows(year)
        title = "Statistiken Export"
    elif export_type == "interest":
        rows = interest_export_rows(year)
        title = "Zinsen Export"
    elif export_type == "cash_audits":
        rows = cash_audit_export_rows(year)
        title = "Kassenprüfung Export"
    else:
        flash("Unbekannter Exportbereich.", "danger")
        return redirect(url_for("exports_page"))

    headers = export_headers(export_type)
    audit_log(
        "system",
        "export_created",
        f"Export erstellt: {title}",
        details=f"Bereich: {title}\nFormat: {fmt}\nJahr: {year or 'Alle'}\nDatensätze: {len(rows)}",
        object_type="Export",
        new_value=title,
    )
    db.session.commit()
    return export_response(rows, headers, export_type, fmt, title)


@app.route("/audit-log", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def audit_log_page():
    if request.method == "POST":
        if current_user.role != "admin":
            flash("Nur Admins können das Revisionsprotokoll bereinigen.", "danger")
            return redirect(url_for("audit_log_page"))

        form_action = request.form.get("form_action", "")

        if form_action == "audit_log_retention_settings":
            old_value = safe_int_setting("audit_log_retention_days", 0, 0)
            try:
                new_value = int(request.form.get("audit_log_retention_days", "0") or "0")
            except ValueError:
                new_value = 0
            if new_value not in (0, 90, 180, 365, 730, 1825):
                new_value = 0
            set_setting_value("audit_log_retention_days", str(new_value))
            audit_log(
                "system",
                "audit_log_retention_changed",
                "Aufbewahrungsfrist fürs Revisionsprotokoll geändert",
                object_type="AppSetting",
                old_value=f"{old_value} Tage" if old_value else "aus",
                new_value=f"{new_value} Tage" if new_value else "aus",
            )
            db.session.commit()
            flash("Aufbewahrungsfrist wurde gespeichert.", "success")
            return redirect(url_for("audit_log_page"))

        if form_action == "audit_log_delete_entries":
            try:
                selected_ids = [int(value) for value in request.form.getlist("selected_entries")]
            except ValueError:
                selected_ids = []
            if not selected_ids:
                flash("Es wurde kein Eintrag zum Löschen ausgewählt.", "warning")
                return redirect(url_for("audit_log_page"))

            # Reihenfolge wichtig: erst zählen/loggen, dann löschen. Die
            # audit_logs-Tabelle nutzt SQLite-Rowids ohne AUTOINCREMENT - ein
            # NACH dem Löschen eingefügter Log-Eintrag würde sonst sofort die
            # ID der gerade gelöschten (höchsten) Zeile wiederverwenden und
            # beim Nachschlagen wie ein nie gelöschter Eintrag aussehen.
            matched_ids = [
                row.id for row in AuditLog.query.filter(AuditLog.id.in_(selected_ids)).all()
            ]
            audit_log(
                "system",
                "audit_log_entries_deleted",
                f"{len(matched_ids)} Revisionsprotokoll-Eintrag(e) manuell gelöscht",
                object_type="AuditLog",
            )
            if matched_ids:
                AuditLog.query.filter(AuditLog.id.in_(matched_ids)).delete(synchronize_session=False)
            db.session.commit()
            flash(f"{len(matched_ids)} Eintrag(e) wurden gelöscht.", "success")
            return redirect(url_for("audit_log_page"))

        flash("Unbekannte Aktion.", "danger")
        return redirect(url_for("audit_log_page"))

    category = request.args.get("category", "").strip()
    user = request.args.get("user", "").strip()
    q = request.args.get("q", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    object_type = request.args.get("object_type", "").strip()
    object_id_raw = request.args.get("object_id", "").strip()

    query = AuditLog.query
    if category:
        query = query.filter(AuditLog.category == category)
    if user:
        query = query.filter(AuditLog.username == user)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                AuditLog.title.ilike(like),
                AuditLog.details.ilike(like),
                AuditLog.old_value.ilike(like),
                AuditLog.new_value.ilike(like),
            )
        )
    if date_from:
        try:
            query = query.filter(AuditLog.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            flash("Filter ignoriert: Datum von ist ungültig.", "warning")
            date_from = ""
    if date_to:
        try:
            query = query.filter(AuditLog.created_at < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            flash("Filter ignoriert: Datum bis ist ungültig.", "warning")
            date_to = ""

    object_id = None
    if object_type and object_id_raw:
        try:
            object_id = int(object_id_raw)
        except ValueError:
            object_id = None
        if object_id is not None:
            query = query.filter(AuditLog.object_type == object_type, AuditLog.object_id == object_id)

    entries = query.order_by(AuditLog.created_at.desc()).limit(300).all()
    categories = [
        ("", "Alle Bereiche"),
        ("finance", "Finanzen"),
        ("event", "Kegelabende"),
        ("member", "Mitglieder"),
        ("penalty_type", "Strafarten"),
        ("settings", "Einstellungen"),
        ("annual_closing", "Jahresabschluss"),
        ("interest", "Zinsen"),
        ("Dokumente", "Dokumente"),
        ("Datensicherung", "Datensicherung"),
        ("Vereins-Import", "Vereins-Import"),
        ("Vereins-Export", "Vereins-Export"),
        ("system", "System"),
    ]
    users = [row[0] for row in db.session.query(AuditLog.username).filter(AuditLog.username.isnot(None)).distinct().order_by(AuditLog.username).all()]
    return render_template(
        "audit_log.html",
        entries=entries,
        categories=categories,
        users=users,
        selected_category=category,
        selected_user=user,
        q=q,
        date_from=date_from,
        date_to=date_to,
        object_filter_label=audit_object_label(object_type, object_id) if object_id else None,
        retention_days=safe_int_setting("audit_log_retention_days", 0, 0),
        retention_eligible_categories=sorted(RETENTION_ELIGIBLE_CATEGORIES),
    )


@app.before_request
def maybe_cleanup_audit_log():
    # Sehr leichte eingebaute Automatik, gleiches Muster wie
    # maybe_create_scheduled_backup() in routes/backups.py: läuft höchstens
    # einmal täglich, beim ersten Request nach Mitternacht.
    if request.endpoint == "static":
        return
    try:
        retention_days = safe_int_setting("audit_log_retention_days", 0, 0)
        if not retention_days:
            return

        last_value = setting_value("audit_log_last_cleanup", "") or ""
        today = datetime.now().date()
        if last_value:
            try:
                if datetime.fromisoformat(last_value).date() >= today:
                    return
            except ValueError:
                pass

        # Erst zählen/loggen, dann löschen - siehe Kommentar bei
        # cleanup_old_audit_log_entries() zum SQLite-Rowid-Wiederverwendungs-Risiko.
        pending = count_old_audit_log_entries(retention_days)
        if pending:
            audit_log(
                "system",
                "audit_log_cleanup",
                f"{pending} alte Revisionsprotokoll-Eintrag(e) automatisch gelöscht",
                details=f"Aufbewahrungsfrist: {retention_days} Tage",
                object_type="AuditLog",
            )
        cleanup_old_audit_log_entries(retention_days)
        set_setting_value("audit_log_last_cleanup", datetime.now().isoformat(timespec="seconds"))
        db.session.commit()
    except Exception:
        db.session.rollback()
