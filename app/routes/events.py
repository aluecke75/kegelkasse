"""Kegelabende: Anlegen, Strafen/Anwesenheit erfassen, Abrechnen (Barzahlungen,
Bahnkosten), Bearbeitungssperre, persönliche Sicht ("Mein Kegelabend"/"Meine Statistik")."""
import re
import secrets
from datetime import datetime, timedelta

from flask import request, flash, redirect, url_for, render_template, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import app
from auth import role_required
from models import (
    db, Member, BowlingEvent, EventParticipant, EventEditLock, ParticipantPenalty,
    PenaltyType, MemberPenaltyTransaction, AccountTransaction, CashbookEntry, Document,
)
from services.dates import add_month, last_weekday_of_month, nth_weekday_of_month, last_day_of_month
from services.money import cents_to_euro, form_euro_to_cents, round_to_ten_cents
from services.audit import audit_log, audit_value, audit_diff_lines, event_status_label
from services.settings import setting_value
from routes.cashbook import account_balance
from routes.penalty_balances import member_penalty_balance
from routes.documents import document_allowed, DOCUMENT_DIR


def event_audit_snapshot(event):
    snapshot = {
        "Datum": event.event_date.isoformat() if event.event_date else "",
        "Status": event_status_label(event.status),
        "Bahnkosten": f"{cents_to_euro(event.lane_cost_cents)} €",
        "Notiz/Grund": event.note or "",
    }

    participants = EventParticipant.query.filter_by(event_id=event.id).all()
    penalty_types = PenaltyType.query.order_by(PenaltyType.sort_order, PenaltyType.name).all()

    for participant in sorted(participants, key=lambda item: participant_display_name(item).casefold()):
        name = participant_display_name(participant)
        snapshot[f"{name} · Status"] = event_status_label(participant.status)

        for penalty_type in penalty_types:
            penalty = ParticipantPenalty.query.filter_by(
                participant_id=participant.id,
                penalty_type_id=penalty_type.id,
            ).first()
            if not penalty:
                value = "0,00 €" if penalty_type.kind == "amount" else "0"
            elif penalty_type.kind == "amount":
                note = f" ({penalty.note})" if penalty.note else ""
                value = f"{cents_to_euro(penalty.amount_cents)} €{note}"
            else:
                value = str(penalty.quantity or 0)
            snapshot[f"{name} · {penalty_type.name}"] = value

    return snapshot


def next_event_date_from_rhythm(reference_date=None):
    today = reference_date or datetime.today().date()
    rhythm_type = setting_value("event_rhythm_type", "weeks")

    latest_event = BowlingEvent.query.order_by(BowlingEvent.event_date.desc()).first()
    anchor = latest_event.event_date if latest_event else today

    if rhythm_type in ("monthly_first_weekday", "monthly_nth_weekday", "monthly_last_weekday"):
        try:
            weekday = int(setting_value("event_rhythm_weekday", "4"))
        except ValueError:
            weekday = 4
        weekday = min(max(weekday, 0), 6)

        try:
            nth = int(setting_value("event_rhythm_nth", "1"))
        except ValueError:
            nth = 1
        nth = min(max(nth, 1), 4)

        year, month = today.year, today.month
        while True:
            if rhythm_type == "monthly_last_weekday":
                candidate = last_weekday_of_month(year, month, weekday)
            else:
                candidate = nth_weekday_of_month(year, month, weekday, nth)
                if candidate.month != month:
                    candidate = last_weekday_of_month(year, month, weekday)
            if candidate >= today:
                return candidate
            year, month = add_month(year, month)

    if rhythm_type == "monthly_day":
        try:
            day = int(setting_value("event_rhythm_month_day", "1"))
        except ValueError:
            day = 1
        day = min(max(day, 1), 31)
        year, month = today.year, today.month
        while True:
            candidate_day = min(day, last_day_of_month(year, month))
            candidate = datetime(year, month, candidate_day).date()
            if candidate >= today:
                return candidate
            year, month = add_month(year, month)

    try:
        weeks = int(setting_value("event_rhythm_weeks", "4"))
    except ValueError:
        weeks = 4
    weeks = max(1, weeks)
    try:
        weekday = int(setting_value("event_rhythm_weekday", "4"))
    except ValueError:
        weekday = 4
    weekday = min(max(weekday, 0), 6)

    # Bei Wochen-Rhythmus zusätzlich den gewünschten Wochentag beachten.
    # Ohne vorhandenen Abend nehmen wir den nächsten passenden Wochentag ab heute.
    if not latest_event:
        offset = (weekday - today.weekday()) % 7
        return today + timedelta(days=offset)

    step = timedelta(weeks=weeks)
    candidate = anchor + step
    candidate = candidate + timedelta(days=(weekday - candidate.weekday()) % 7)
    while candidate < today:
        candidate = candidate + step
    return candidate


def next_event_label(next_date):
    today = datetime.today().date()
    if next_date == today:
        return "heute"
    delta = (next_date - today).days
    if delta > 0:
        return f"in {delta} Tagen"
    return f"vor {abs(delta)} Tagen"


def average_rounding_label(raw_cents, rounded_cents):
    if raw_cents == rounded_cents:
        return "exakt auf 0,10 €"
    if rounded_cents > raw_cents:
        return "aufgerundet auf 0,10 €"
    return "abgerundet auf 0,10 €"


def event_has_started_entries(event):
    """Prüft, ob für den Abend schon echte Erfassungsdaten vorhanden sind.

    Gäste allein zählen noch nicht, damit mehrere Gäste nacheinander angelegt werden können.
    """
    participants = EventParticipant.query.filter_by(event_id=event.id).all()

    for participant in participants:
        for penalty in participant.dynamic_penalties:
            if (penalty.quantity or 0) > 0:
                return True
            if (penalty.amount_cents or 0) > 0:
                return True
            if penalty.note:
                return True

    return False


def active_penalty_types():
    return PenaltyType.query.filter_by(active=True).order_by(
        PenaltyType.sort_order,
        PenaltyType.name,
    ).all()


def get_or_create_participant_penalty(participant, penalty_type):
    penalty = ParticipantPenalty.query.filter_by(
        participant_id=participant.id,
        penalty_type_id=penalty_type.id,
    ).first()

    if not penalty:
        penalty = ParticipantPenalty(
            participant_id=participant.id,
            penalty_type_id=penalty_type.id,
            quantity=0,
            amount_cents=0,
        )
        db.session.add(penalty)
        db.session.flush()

    return penalty


def participant_display_name(participant):
    """Anzeigename: Spitzname bevorzugt, sonst Vorname."""
    if participant.member:
        return participant.member.nickname or participant.member.first_name or participant.member.last_name

    return participant.guest_name or "Gast"


def participant_sort_name(participant):
    """Sortierung aktuell bewusst nach Vorname; später per Einstellung erweiterbar."""
    if participant.member:
        return (participant.member.first_name or participant.member.nickname or participant.member.last_name or "").casefold()

    return (participant.guest_name or "Gast").casefold()


def participant_sort_key(row):
    """Anwesende oben, Gäste danach, Fehlende unten, dann nach Vorname."""
    status_order = {
        "present": 0,
        "guest": 1,
        "excused": 2,
        "unexcused": 2,
    }

    participant = row["participant"]
    return (
        status_order.get(participant.status, 9),
        participant_sort_name(participant),
        row["display_name"].casefold(),
    )


def penalty_entry_amount_cents(penalty):
    if not penalty.penalty_type or not penalty.penalty_type.active:
        return 0
    if penalty.penalty_type.kind == "amount":
        return penalty.amount_cents or 0
    return (penalty.quantity or 0) * (penalty.penalty_type.amount_cents or 0)


def participant_base_penalty_cents(participant):
    """Nur echte Strafbeträge ohne Gastbeitrag und ohne Fehlgeld.

    target_mode self: Der Spieler zahlt die eigene Strafe.
    target_mode others: Alle anderen anwesenden Spieler/Gäste zahlen diese Strafe.
    """
    total = 0

    if participant.status not in ("present", "guest"):
        return 0

    # Eigene Strafen, die der Spieler selbst zahlt.
    for penalty in participant.dynamic_penalties:
        if getattr(penalty.penalty_type, "target_mode", "self") == "others":
            continue
        total += penalty_entry_amount_cents(penalty)

    # Strafen anderer Spieler, die alle anderen zahlen.
    others = EventParticipant.query.filter(
        EventParticipant.event_id == participant.event_id,
        EventParticipant.id != participant.id,
        EventParticipant.status.in_(("present", "guest")),
    ).all()

    for other in others:
        for penalty in other.dynamic_penalties:
            if getattr(penalty.penalty_type, "target_mode", "self") != "others":
                continue
            total += penalty_entry_amount_cents(penalty)

    return total


def event_average_present_penalty_raw_cents(event):
    """Ungerundete Durchschnittsstrafe der Anwesenden/Gäste, ohne Gastbeitrag."""
    present_participants = EventParticipant.query.filter(
        EventParticipant.event_id == event.id,
        EventParticipant.status.in_(("present", "guest")),
    ).all()

    if not present_participants:
        return 0

    total = sum(participant_base_penalty_cents(participant) for participant in present_participants)
    return int(round(total / len(present_participants)))


def event_average_present_penalty_cents(event):
    """Auf 0,10 € gerundete Durchschnittsstrafe der Anwesenden/Gäste, ohne Gastbeitrag."""
    return round_to_ten_cents(event_average_present_penalty_raw_cents(event))


def participant_penalty_cents(participant, target_date, average_present_cents=0):
    """Berechnet Strafen/Gebühren für einen Teilnehmer in Cent."""
    from app import current_rate_cents, guest_penalties_charged

    total = 0

    if participant.status == "guest":
        total += current_rate_cents("guest_fee", target_date)
        if guest_penalties_charged(target_date):
            total += participant_base_penalty_cents(participant)

    elif participant.status == "present":
        total += participant_base_penalty_cents(participant)

    elif participant.status == "excused":
        total += average_present_cents
        total += current_rate_cents("absence_excused", target_date)

    elif participant.status == "unexcused":
        total += average_present_cents
        total += current_rate_cents("absence_unexcused", target_date)

    return total


def participant_penalty_breakdown(participant):
    """Aufteilung der sichtbaren Strafwerte für die persönliche Nur-Lese-Ansicht.

    Bei Strafarten mit target_mode="others" wird getrennt angezeigt:
    - wie oft das Mitglied selbst das Ereignis ausgelöst/geworfen hat
    - wie oft es bezahlen muss, weil andere Spieler das Ereignis ausgelöst haben
    """
    if not participant:
        return []

    items = []
    penalty_types = active_penalty_types()

    for penalty_type in penalty_types:
        target_mode = getattr(penalty_type, "target_mode", "self")
        cents = 0
        quantity = 0
        own_quantity = 0
        payable_quantity = 0
        note = None

        if target_mode == "others":
            own_penalty = ParticipantPenalty.query.filter_by(
                participant_id=participant.id,
                penalty_type_id=penalty_type.id,
            ).first()
            if own_penalty:
                own_quantity = own_penalty.quantity or 0
                if own_penalty.note:
                    note = own_penalty.note

            others = EventParticipant.query.filter(
                EventParticipant.event_id == participant.event_id,
                EventParticipant.id != participant.id,
                EventParticipant.status.in_(("present", "guest")),
            ).all()
            for other in others:
                penalty = ParticipantPenalty.query.filter_by(
                    participant_id=other.id,
                    penalty_type_id=penalty_type.id,
                ).first()
                if penalty:
                    cents += penalty_entry_amount_cents(penalty)
                    payable_quantity += penalty.quantity or 0
                    if penalty.note and not note:
                        note = penalty.note

            quantity = payable_quantity
        else:
            penalty = ParticipantPenalty.query.filter_by(
                participant_id=participant.id,
                penalty_type_id=penalty_type.id,
            ).first()
            if penalty:
                cents += penalty_entry_amount_cents(penalty)
                quantity += penalty.quantity or 0
                note = penalty.note

        items.append({
            "name": penalty_type.name,
            "kind": penalty_type.kind,
            "target_mode": target_mode,
            "quantity": quantity,
            "own_quantity": own_quantity,
            "payable_quantity": payable_quantity,
            "amount_cents": cents,
            "amount_euro": cents_to_euro(cents),
            "note": note,
        })

    return items


def event_export_candidates():
    """Abgeschlossene Kegelabende für den Protokoll-Export, neueste zuerst.

    Bewusst nur "closed" - ein gerade laufender Abend (open/settlement/lane_cost)
    oder ein ausgefallener (cancelled) soll hier nie als Vorschlag oder Auswahl
    auftauchen, siehe Nutzerwunsch "niemals ein grade aktiver".
    """
    return BowlingEvent.query.filter_by(status="closed").order_by(
        BowlingEvent.event_date.desc(), BowlingEvent.id.desc()
    ).all()


def _pdf_check_mark_cmds(x, y, rgb):
    r, g, b = rgb
    return [
        f"{r} {g} {b} RG",
        "1.4 w",
        f"{x:.1f} {y + 2.3:.1f} m {x + 2.3:.1f} {y:.1f} l {x + 6.5:.1f} {y + 6.5:.1f} l S",
        "0 g",
    ]


def _pdf_cross_mark_cmds(x, y, rgb):
    r, g, b = rgb
    return [
        f"{r} {g} {b} RG",
        "1.4 w",
        f"{x:.1f} {y:.1f} m {x + 6:.1f} {y + 6.5:.1f} l S",
        f"{x:.1f} {y + 6.5:.1f} m {x + 6:.1f} {y:.1f} l S",
        "0 g",
    ]


def build_event_protocol_pdf(event):
    """Einseitiges (bei Bedarf mehrseitiges) Kegelabend-Protokoll zum Ausdrucken,
    z. B. als Papier-Rückfallebene falls die App mal nicht erreichbar ist.

    Nur für abgeschlossene Abende gedacht, siehe event_export_candidates().
    Häkchen/Kreuz werden als kleine Vektor-Linien gezeichnet statt als Unicode-
    Zeichen, weil die eingebauten PDF-Basisschriften (WinAnsiEncoding/cp1252)
    keine ✓/✗-Glyphen enthalten.
    """
    from services.pdf import _pdf_text_cmd, _pdf_assemble, get_logo_pdf_image
    from routes.cashbook import account_balance_as_of

    page_w, page_h = 842, 595
    margin = 40
    logo_image = get_logo_pdf_image()

    penalty_types = active_penalty_types()
    status_order = {"present": 0, "guest": 1, "excused": 2, "unexcused": 3}
    participants = sorted(
        EventParticipant.query.filter_by(event_id=event.id).all(),
        key=lambda p: (status_order.get(p.status, 4), participant_display_name(p).lower()),
    )
    average_present_cents = event_average_present_penalty_cents(event)

    rows = []
    total_penalty_cents = 0
    counts = {"present": 0, "excused": 0, "unexcused": 0, "guest": 0}
    for participant in participants:
        counts[participant.status] = counts.get(participant.status, 0) + 1
        breakdown = participant_penalty_breakdown(participant)
        cells = []
        for item in breakdown:
            own_qty = item["own_quantity"] if item["target_mode"] == "others" else item["quantity"]
            if item["kind"] == "amount":
                cells.append(item["amount_euro"] + " €" if item["amount_cents"] else None)
            else:
                cells.append(str(own_qty) if own_qty else None)
        total_cents = participant_penalty_cents(participant, event.event_date, average_present_cents)
        total_penalty_cents += total_cents
        rows.append({
            "name": participant_display_name(participant),
            "status": participant.status,
            "cells": cells,
            "sum_euro": cents_to_euro(total_cents),
        })

    club_name = setting_value("club_name", "Kegelkasse") or "Kegelkasse"
    now_text = datetime.now().strftime("%d.%m.%Y %H:%M")
    cash_after_cents = account_balance_as_of("cash", event.event_date)

    guest_count = counts.get("guest", 0)
    guest_word = "Gast" if guest_count == 1 else "Gäste"
    attendance_summary = (
        f"{counts.get('present', 0)} anwesend · {counts.get('excused', 0)} entschuldigt · "
        f"{counts.get('unexcused', 0)} unentschuldigt · {guest_count} {guest_word}"
    )

    name_w, attend_w, sum_w = 130, 55, 70
    usable = page_w - 2 * margin
    type_area = usable - name_w - attend_w - sum_w
    type_width = type_area / max(1, len(penalty_types))

    def type_x(i):
        return margin + name_w + attend_w + i * type_width

    sum_x = page_w - margin - sum_w
    meta_col_w = usable / 4

    pages_cmds = []
    cmds = []

    def draw_page_head():
        cmds.append("0 g")
        cmds.append(_pdf_text_cmd(margin, page_h - 26, club_name, 16, "F2"))
        cmds.append("0.36 0.36 0.30 rg")
        cmds.append(_pdf_text_cmd(margin, page_h - 40, "KEGELABEND-PROTOKOLL - PAPIERABLAGE", 8, "F1"))
        cmds.append("0 g")

        if logo_image:
            display_height = 32
            display_width = min(110, display_height * (logo_image["width"] / logo_image["height"]))
            logo_x = page_w - margin - display_width
            logo_y = page_h - 44
            cmds.append("q")
            cmds.append(f"{display_width:.2f} 0 0 {display_height:.2f} {logo_x:.2f} {logo_y:.2f} cm")
            cmds.append("/Logo Do")
            cmds.append("Q")

        cmds.append("0.12 0.48 0.23 RG")
        cmds.append("2 w")
        cmds.append(f"{margin} {page_h - 52} m {page_w - margin} {page_h - 52} l S")
        cmds.append("0 g")

        meta_y_label = page_h - 68
        meta_y_value = page_h - 80
        meta_row_w = usable / 3
        meta_items = [
            ("DATUM", event.event_date.strftime("%d.%m.%Y")),
            ("AUSGEDRUCKT AM", now_text + " Uhr"),
            ("STATUS", "Abgeschlossen"),
        ]
        for i, (label, value) in enumerate(meta_items):
            x = margin + i * meta_row_w
            cmds.append("0.42 0.46 0.40 rg")
            cmds.append(_pdf_text_cmd(x, meta_y_label, label, 6.5, "F1"))
            cmds.append("0 g")
            cmds.append(_pdf_text_cmd(x, meta_y_value, value, 9.5, "F2"))

        cmds.append("0.30 0.34 0.28 rg")
        cmds.append(_pdf_text_cmd(margin, page_h - 92, "Teilnehmer: " + attendance_summary, 8, "F1"))
        cmds.append("0 g")

        legend_y = page_h - 108
        legend_items = [
            ((0.12, 0.48, 0.23), "check", "anwesend"),
            ((0.12, 0.48, 0.23), "check", "Gast"),
            ((0.63, 0.36, 0.0), "cross", "entschuldigt"),
            ((0.63, 0.15, 0.15), "cross", "unentschuldigt"),
        ]
        lx = margin
        for color, kind, label in legend_items:
            if kind == "check":
                cmds.extend(_pdf_check_mark_cmds(lx, legend_y - 1, color))
            else:
                cmds.extend(_pdf_cross_mark_cmds(lx, legend_y - 1, color))
            cmds.append("0.42 0.46 0.40 rg")
            cmds.append(_pdf_text_cmd(lx + 11, legend_y, label, 7, "F1"))
            cmds.append("0 g")
            lx += 11 + len(label) * 4.2 + 16

        header_y = page_h - 128
        type_col_max_chars = max(6, int((type_width - 6) / 4.3))
        cmds.append("0.42 0.46 0.40 rg")
        cmds.append(_pdf_text_cmd(margin + 2, header_y, "TEILNEHMER", 6.5, "F1"))
        cmds.append(_pdf_text_cmd(margin + name_w + 2, header_y, "ANW.", 6.5, "F1"))
        for i, penalty_type in enumerate(penalty_types):
            cmds.append(_pdf_text_cmd(type_x(i) + 2, header_y, penalty_type.name.upper()[:type_col_max_chars], 6.5, "F1"))
            if penalty_type.kind != "amount":
                cmds.append(_pdf_text_cmd(type_x(i) + 2, header_y - 8, f"{cents_to_euro(penalty_type.amount_cents)} €/Stk.", 5.5, "F1"))
        cmds.append(_pdf_text_cmd(sum_x + sum_w - 30, header_y, "SUMME", 6.5, "F1"))
        cmds.append("0 g")

        cmds.append("0.14 0.16 0.11 RG")
        cmds.append("1 w")
        rule_y = header_y - 14
        cmds.append(f"{margin} {rule_y} m {page_w - margin} {rule_y} l S")
        return rule_y - 16

    def draw_page_foot():
        cmds.append("0.55 0.55 0.5 rg")
        cmds.append(_pdf_text_cmd(margin, 26, "Kegelkasse - Kegelabend-Protokoll", 7, "F1"))
        cmds.append("0 g")

    y_val = draw_page_head()
    row_height = 15
    page_bottom = 140  # Platz für Fußnote/Summenzeile/Unterschriftenzeile auf der letzten Seite reservieren

    for row in rows:
        if y_val < page_bottom:
            draw_page_foot()
            pages_cmds.append(cmds)
            cmds = []
            y_val = draw_page_head()

        cmds.append(_pdf_text_cmd(margin + 2, y_val, row["name"][:26], 8, "F1"))

        mark_x = margin + name_w + 18
        mark_y = y_val - 2
        if row["status"] in ("present", "guest"):
            cmds.extend(_pdf_check_mark_cmds(mark_x, mark_y, (0.12, 0.48, 0.23)))
        elif row["status"] == "excused":
            cmds.extend(_pdf_cross_mark_cmds(mark_x, mark_y, (0.63, 0.36, 0.0)))
        else:
            cmds.extend(_pdf_cross_mark_cmds(mark_x, mark_y, (0.63, 0.15, 0.15)))

        for i, cell in enumerate(row["cells"]):
            cmds.append(_pdf_text_cmd(type_x(i) + 2, y_val, cell if cell else "-", 8, "F1"))

        sum_text = row["sum_euro"] + " €"
        cmds.append(_pdf_text_cmd(sum_x + sum_w - 8 - len(sum_text) * 4.4, y_val, sum_text, 8.5, "F2"))

        cmds.append("0.88 0.87 0.82 RG")
        cmds.append("0.5 w")
        line_y = y_val - 5
        cmds.append(f"{margin} {line_y} m {page_w - margin} {line_y} l S")
        cmds.append("0 g")

        y_val -= row_height

    y_val -= 6
    cmds.append("0.42 0.46 0.40 rg")
    footnote_lines = [
        "Bei Strafarten mit \"alle anderen zahlen\" (z. B. Kranz, Alle Neune) zeigt die Tabelle nur, wer es selbst ausgelöst hat;",
        "die Spalte \"Summe\" enthält bereits den korrekt verrechneten Endbetrag inkl. Fehlgeld/Gastbeitrag/Rundenanteil.",
    ]
    for line in footnote_lines:
        cmds.append(_pdf_text_cmd(margin, y_val, line, 7, "F1"))
        y_val -= 10
    cmds.append("0 g")
    y_val -= 8

    summary_items = [
        ("BAHNKOSTEN DIESEN ABEND", f"{event.lane_cost_euro()} €"),
        ("STRAFEN & GEBÜHREN GESAMT", f"{cents_to_euro(total_penalty_cents)} €"),
        ("BARKASSE NACH DIESEM ABEND", f"{cents_to_euro(cash_after_cents)} €"),
        ("TEILNEHMER GESAMT", str(len(rows))),
    ]
    cmds.append("0.14 0.16 0.11 RG")
    cmds.append("1 w")
    cmds.append(f"{margin} {y_val} m {page_w - margin} {y_val} l S")
    y_val -= 16
    for i, (label, value) in enumerate(summary_items):
        x = margin + i * meta_col_w
        cmds.append("0.42 0.46 0.40 rg")
        cmds.append(_pdf_text_cmd(x, y_val, label, 6.5, "F1"))
        cmds.append("0.10 0.35 0.18 rg" if i < 3 else "0 g")
        cmds.append(_pdf_text_cmd(x, y_val - 13, value, 10, "F2"))
        cmds.append("0 g")
    y_val -= 34

    sign_col_w = 220
    cmds.append("0.6 0.6 0.55 RG")
    cmds.append("0.7 w")
    cmds.append(f"{margin} {y_val} m {margin + sign_col_w} {y_val} l S")
    cmds.append(f"{margin + sign_col_w + 30} {y_val} m {margin + 2 * sign_col_w + 30} {y_val} l S")
    cmds.append("0.42 0.46 0.40 rg")
    cmds.append(_pdf_text_cmd(margin, y_val - 10, "Kegelwart", 7, "F1"))
    cmds.append(_pdf_text_cmd(margin + sign_col_w + 30, y_val - 10, "Datum, Unterschrift", 7, "F1"))
    cmds.append("0 g")

    draw_page_foot()
    pages_cmds.append(cmds)

    return _pdf_assemble(pages_cmds, page_width=page_w, page_height=page_h, logo_image=logo_image)


def current_member_for_user():
    if not current_user.is_authenticated:
        return None
    return Member.query.filter_by(user_id=current_user.id).first()


def personal_event_payload():
    member = current_member_for_user()
    if not member:
        return {
            "has_member": False,
            "message": "Dein Benutzerkonto ist noch keinem Mitglied zugeordnet.",
        }

    event = get_active_event()
    is_live = True

    if not event:
        event = BowlingEvent.query.filter(BowlingEvent.status == "closed").order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc()).first()
        is_live = False

    if not event:
        return {
            "has_member": True,
            "has_event": False,
            "message": "Es wurde noch kein Kegelabend gefunden.",
        }

    participant = EventParticipant.query.filter_by(event_id=event.id, member_id=member.id).first()
    if not participant:
        return {
            "has_member": True,
            "has_event": True,
            "event_date": event.event_date.strftime("%d.%m.%Y"),
            "is_live": is_live,
            "message": "Für dich wurde bei diesem Kegelabend kein Eintrag gefunden.",
        }

    average_present_cents = event_average_present_penalty_cents(event)
    total_cents = participant_penalty_cents(participant, event.event_date, average_present_cents)
    stored_balance_cents = member_penalty_balance(member.id)
    # Bei laufenden Abenden sind die Strafwerte noch nicht endgültig im Strafkonto gebucht.
    # Für die Mitgliederansicht zeigen wir deshalb live: bisher offen + aktueller Abend.
    balance_cents = stored_balance_cents + total_cents if is_live and event.status == "open" else stored_balance_cents
    lock = EventEditLock.query.filter_by(event_id=event.id).first() if is_live else None
    editor = lock.user.username if lock and lock.user else None

    return {
        "has_member": True,
        "has_event": True,
        "event_id": event.id,
        "event_date": event.event_date.strftime("%d.%m.%Y"),
        "is_live": is_live,
        "editor": editor,
        "status": participant.status,
        "status_label": {
            "present": "Anwesend",
            "guest": "Gast",
            "excused": "Fehlt entschuldigt",
            "unexcused": "Fehlt unentschuldigt",
        }.get(participant.status, participant.status),
        "items": participant_penalty_breakdown(participant),
        "current_total_cents": total_cents,
        "current_total_euro": cents_to_euro(total_cents),
        "balance_cents": balance_cents,
        "balance_euro": cents_to_euro(balance_cents),
        "message": None,
    }


def can_edit_closed_events():
    return current_user.role in ("admin", "cashier")


def can_write_running_events():
    return current_user.role in ("admin", "cashier", "member")


def can_override_event_lock():
    return current_user.role in ("admin", "cashier")


def acquire_event_lock(event):
    """Sperrt einen laufenden Kegelabend kurzzeitig für einen bearbeitenden Benutzer.

    Die Sperre wird bei jedem Laden/Speichern verlängert. Läuft der Browser weg
    oder wird geschlossen, verfällt die Sperre automatisch nach wenigen Minuten.
    """
    if event.status in ("closed", "cancelled"):
        return True, None

    if not can_write_running_events():
        return False, None

    now = datetime.utcnow()
    locked_until = now + timedelta(minutes=5)

    lock = EventEditLock.query.filter_by(event_id=event.id).first()

    if lock and lock.locked_until < now:
        db.session.delete(lock)
        db.session.commit()
        lock = None

    if lock and lock.user_id != current_user.id:
        return False, lock

    if not lock:
        lock = EventEditLock(
            event_id=event.id,
            user_id=current_user.id,
            locked_at=now,
            locked_until=locked_until,
        )
        db.session.add(lock)
    else:
        lock.locked_at = now
        lock.locked_until = locked_until

    db.session.commit()
    return True, lock


def release_event_lock(event):
    EventEditLock.query.filter_by(
        event_id=event.id,
        user_id=current_user.id,
    ).delete()


@app.route("/events/<int:event_id>/lock/ping", methods=["POST"])
@login_required
def event_lock_ping(event_id):
    event = BowlingEvent.query.get_or_404(event_id)
    lock_allowed, active_lock = acquire_event_lock(event)
    if not lock_allowed:
        editor = active_lock.user.username if active_lock and active_lock.user else "anderem Benutzer"
        return jsonify({
            "ok": False,
            "message": f"Dieser Kegelabend wird gerade von {editor} bearbeitet.",
            "editor": editor,
        }), 409
    return jsonify({"ok": True})


@app.route("/events/<int:event_id>/lock/release", methods=["POST"])
@login_required
def event_lock_release(event_id):
    event = BowlingEvent.query.get_or_404(event_id)
    release_event_lock(event)
    db.session.commit()
    return ("", 204)


def save_event_participants(event):
    participants = EventParticipant.query.filter_by(event_id=event.id).all()
    penalty_types = active_penalty_types()

    for participant in participants:
        prefix = f"participant_{participant.id}_"

        if participant.member_id:
            requested_status = request.form.get(prefix + "status", participant.status)
            if requested_status in ("present", "excused", "unexcused"):
                participant.status = requested_status
            else:
                participant.status = "present"
        else:
            participant.status = "guest"

        for penalty_type in penalty_types:
            penalty = get_or_create_participant_penalty(participant, penalty_type)
            field_prefix = f"penalty_{participant.id}_{penalty_type.id}"

            if penalty_type.kind == "amount":
                penalty.amount_cents = form_euro_to_cents(field_prefix + "_amount", penalty_type.name)
                penalty.quantity = 1 if penalty.amount_cents else 0
                penalty.note = request.form.get(field_prefix + "_note", "").strip() or None
            else:
                quantity_raw = request.form.get(field_prefix + "_quantity", "0") or "0"
                if not str(quantity_raw).isdigit():
                    flash(f"Bitte bei '{penalty_type.name}' nur ganze Zahlen eingeben.", "danger")
                    raise ValueError("Ungültige Anzahl")
                penalty.quantity = max(0, int(quantity_raw))
                penalty.amount_cents = 0
                penalty.note = None


def get_active_event():
    """Der aktuell laufende Kegelabend: Status offen/Abrechnung/Bahnkosten,
    unabhängig vom Datum. So funktioniert es seit Einführung dieser Funktion
    (v0.98.38c) und wird bewusst so beibehalten: Kegelabende werden im Verein
    oft schon Tage vorher angelegt, um z.B. bereits bekannte Abmeldungen
    einzutragen - das gilt als aktive Bearbeitung und soll als "Aktiver
    Kegelabend" mit Status-Hinweis erscheinen, nicht als normaler Termin mit
    Countdown. (2026-09-14 kurzzeitig auf "Datum <= heute" eingeschränkt, was
    genau diesen alltäglichen Vorab-Arbeitsablauf kaputt gemacht hat - am
    2026-09-18 auf Nutzerwunsch wieder auf den historischen Stand zurückgesetzt.)
    """
    return (
        BowlingEvent.query
        .filter(BowlingEvent.status.in_(("open", "settlement", "lane_cost")))
        .order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc())
        .first()
    )


def reset_event_bookings(event):
    MemberPenaltyTransaction.query.filter_by(event_id=event.id).delete()

    # Vorfilter per LIKE (nutzt einen Index/ist schnell), aber LIKE '%Kegelabend #1%'
    # trifft auch "Kegelabend #10".."#19", "#100".."#199" usw. Deshalb zusätzlich
    # in Python mit einer Wortgrenze exakt auf diese Event-ID prüfen.
    marker = f"Kegelabend #{event.id}"
    marker_pattern = re.compile(rf"Kegelabend #{event.id}(?!\d)")
    candidate_transactions = AccountTransaction.query.filter(
        AccountTransaction.description.like(f"%{marker}%")
    ).all()
    old_transactions = [
        transaction for transaction in candidate_transactions
        if marker_pattern.search(transaction.description or "")
    ]

    for transaction in old_transactions:
        db.session.delete(transaction)


def clear_event_entry_values(event):
    """Entfernt alle Erfassungswerte eines Abends, z. B. bei Ausfall."""
    participants = EventParticipant.query.filter_by(event_id=event.id).all()

    for participant in participants:
        if participant.member_id:
            participant.status = "present"
        else:
            participant.status = "guest"

        for penalty in participant.dynamic_penalties:
            penalty.quantity = 0
            penalty.amount_cents = 0
            penalty.note = None


@app.route("/my-event")
@login_required
def my_event():
    payload = personal_event_payload()
    return render_template("my_event.html", payload=payload)


@app.route("/my-event/data")
@login_required
def my_event_data():
    return jsonify(personal_event_payload())


@app.route("/my-stats")
@login_required
def my_stats():
    from routes.reports import member_active_years, build_player_overview_rows

    member = current_member_for_user()
    if not member:
        return render_template("my_stats.html", has_member=False)

    years = member_active_years(member.id)
    year_rows = [build_player_overview_rows(year, member_id=member.id)[0] for year in years]

    chart_data = {
        "penaltyByYear": {
            "groups": [str(year) for year in years],
            "series": [{"name": "Strafgeld", "values": [row["penalty_total_cents"] / 100 for row in year_rows]}],
        },
        "attendanceByYear": {
            "groups": [str(year) for year in years],
            "series": [
                {"name": "Anwesend", "values": [row["attended"] for row in year_rows]},
                {"name": "Entschuldigt", "values": [row["excused"] for row in year_rows]},
                {"name": "Unentschuldigt", "values": [row["unexcused"] for row in year_rows]},
            ],
        },
    }

    return render_template(
        "my_stats.html",
        has_member=True,
        member=member,
        years=years,
        year_rows=year_rows,
        chart_data=chart_data,
    )


@app.route("/events")
@login_required
def events():
    event_list = BowlingEvent.query.order_by(BowlingEvent.event_date.desc()).all()

    active_event = get_active_event()

    return render_template(
        "events.html",
        events=event_list,
        active_event=active_event,
    )


@app.route("/events/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "member")
def event_new():
    today = next_event_date_from_rhythm()
    open_event = (
        BowlingEvent.query
        .filter(BowlingEvent.status.in_(("open", "settlement", "lane_cost")))
        .order_by(BowlingEvent.event_date.desc(), BowlingEvent.id.desc())
        .first()
    )

    if request.method == "POST":
        event_date_raw = request.form.get("event_date", "").strip()
        note = request.form.get("note", "").strip()
        is_cancelled = request.form.get("is_cancelled") == "1"
        cancel_reason = request.form.get("cancel_reason", "").strip()
        cancel_note = request.form.get("cancel_note", "").strip()
        force_new = request.form.get("force_new") == "1"

        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()

        if open_event and not is_cancelled and not force_new:
            flash("Es gibt bereits einen offenen Kegelabend. Bitte öffne ihn oder bestätige bewusst, dass du einen neuen Abend anlegen möchtest.", "warning")
            return render_template(
                "event_form.html",
                event=None,
                today=event_date_raw or today.isoformat(),
                form_data=request.form,
                open_event=open_event,
                require_force_new=True,
            )

        if is_cancelled and not cancel_reason:
            flash("Bitte einen Grund angeben, warum der Kegelabend ausfällt.", "danger")
            return render_template(
                "event_form.html",
                event=None,
                today=event_date_raw or today.isoformat(),
                form_data=request.form,
                open_event=open_event,
            )

        if is_cancelled:
            event_note = cancel_reason if not cancel_note else f"{cancel_reason}: {cancel_note}"
        else:
            event_note = note or None

        event = BowlingEvent(
            event_date=event_date,
            lane_cost_cents=0,
            status="cancelled" if is_cancelled else "open",
            note=event_note,
        )

        db.session.add(event)
        db.session.flush()
        audit_log(
            "event",
            "event_created",
            ("Ausgefallener Kegelabend angelegt" if is_cancelled else "Kegelabend angelegt"),
            details=f"Datum: {event.event_date}" + (f"; Grund: {event.note}" if event.note else ""),
            object_type="BowlingEvent",
            object_id=event.id,
        )

        if not is_cancelled:
            active_members = Member.query.filter_by(active=True).order_by(Member.first_name, Member.last_name).all()
            for member in active_members:
                db.session.add(EventParticipant(
                    event_id=event.id,
                    member_id=member.id,
                    status="present",
                ))

        db.session.commit()

        if is_cancelled:
            flash("Ausgefallener Kegelabend wurde angelegt.", "success")
            return redirect(url_for("events"))

        flash("Kegelabend wurde angelegt.", "success")
        return redirect(url_for("event_detail", event_id=event.id))

    return render_template(
        "event_form.html",
        event=None,
        today=today.isoformat(),
        open_event=open_event,
    )


@app.route("/events/<int:event_id>", methods=["GET", "POST"])
@login_required
def event_detail(event_id):
    from app import closed_year_block_message, current_rate_cents

    event = BowlingEvent.query.get_or_404(event_id)

    # Nachträgliche Teilnehmer-Anlage nur als Fallback für einen offenen Abend ohne
    # Teilnehmer (normal legt event_new() sie schon beim Anlegen an). Bewusst nicht
    # für abgeschlossene/ausgefallene Abende (sonst würden importierte Altabende
    # rückwirkend alle aktiven Mitglieder als "anwesend" bekommen und die
    # Anwesenheitsstatistik verfälschen) und nicht für die reine Leserolle
    # "auditor" (ein bloßes Ansehen darf nichts speichern).
    existing_count = EventParticipant.query.filter_by(event_id=event.id).count()
    if existing_count == 0 and event.status == "open" and current_user.role != "auditor":
        active_members = Member.query.filter_by(active=True).order_by(Member.first_name, Member.last_name).all()
        for member in active_members:
            db.session.add(EventParticipant(
                event_id=event.id,
                member_id=member.id,
                status="present",
            ))
        db.session.commit()

    lock_allowed, active_lock = acquire_event_lock(event)

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "take_lock":
            if not can_override_event_lock():
                flash("Nur Admin oder Kassierer/-in dürfen eine Bearbeitungssperre übernehmen.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            EventEditLock.query.filter_by(event_id=event.id).delete()
            db.session.commit()
            acquire_event_lock(event)
            flash("Bearbeitungssperre wurde übernommen. Du kannst diesen Kegelabend jetzt bearbeiten.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if not lock_allowed:
            flash("Dieser Kegelabend wird gerade von einem anderen Benutzer bearbeitet. Du bist im Lesemodus.", "danger")
            return redirect(url_for("event_detail", event_id=event.id))

        if event.status in ("closed", "cancelled") and action != "reopen":
            flash("Dieser Kegelabend ist endgültig abgeschlossen oder ausgefallen und kann nicht mehr geändert werden.", "danger")
            return redirect(url_for("event_detail", event_id=event.id))

        if event.status != "open" and action in ("save", "add_guest", "start_close", "cancel_event"):
            flash("Die Erfassung ist bereits abgeschlossen. Änderungen an Strafen sind jetzt gesperrt.", "danger")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "autosave":
            if event.status != "open":
                return jsonify({"ok": False, "message": "Autosave ist nur bei offenen Kegelabenden möglich."}), 400

            try:
                save_event_participants(event)
            except ValueError as exc:
                db.session.rollback()
                return jsonify({"ok": False, "message": str(exc) or "Eingaben konnten nicht gespeichert werden."}), 400

            db.session.commit()
            return jsonify({
                "ok": True,
                "message": "✓ Änderungen automatisch gespeichert",
                "saved_at": datetime.now().strftime("%H:%M:%S"),
            })

        if action == "save":
            old_snapshot = event_audit_snapshot(event)
            try:
                save_event_participants(event)
            except ValueError:
                db.session.rollback()
                return redirect(url_for("event_detail", event_id=event.id))
            new_snapshot = event_audit_snapshot(event)
            audit_log(
                "event",
                "event_saved",
                f"Kegelabend vom {event.event_date} gespeichert",
                details=audit_diff_lines(old_snapshot, new_snapshot),
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            db.session.commit()
            flash("✓ Änderungen gespeichert", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "add_guest":
            # Beim Gast-Hinzufügen aktuelle Status-Auswahl mit übernehmen, damit
            # z. B. bereits gesetzte Fehlend-Status nicht verloren gehen.
            for participant in EventParticipant.query.filter_by(event_id=event.id).all():
                status = request.form.get(f"participant_{participant.id}_status")
                if status in ["present", "excused", "unexcused"] and participant.member_id:
                    participant.status = status

            if event_has_started_entries(event):
                flash("Gast hinzufügen ist nach den ersten Einträgen gesperrt.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            guest_name = request.form.get("guest_name", "").strip()

            if guest_name:
                guest = EventParticipant(
                    event_id=event.id,
                    guest_name=guest_name,
                    status="guest",
                )
                db.session.add(guest)
                db.session.commit()
                flash("Gast wurde hinzugefügt.", "success")

            return redirect(url_for("event_detail", event_id=event.id))

        if action == "cancel_event":
            old_snapshot = event_audit_snapshot(event)
            cancel_reason = request.form.get("cancel_reason", "").strip()
            cancel_note = request.form.get("cancel_note", "").strip()

            if not cancel_reason:
                flash("Bitte einen Grund für den ausgefallenen Kegelabend angeben.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            reset_event_bookings(event)
            clear_event_entry_values(event)
            event.lane_cost_cents = 0
            event.status = "cancelled"
            event.note = cancel_reason if not cancel_note else f"{cancel_reason}: {cancel_note}"
            new_snapshot = event_audit_snapshot(event)
            audit_log(
                "event",
                "event_cancelled",
                f"Kegelabend vom {event.event_date} als ausgefallen markiert",
                details=audit_diff_lines(old_snapshot, new_snapshot),
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            release_event_lock(event)
            db.session.commit()
            flash("Kegelabend wurde als ausgefallen markiert.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "start_close":
            old_snapshot = event_audit_snapshot(event)
            try:
                save_event_participants(event)
            except ValueError:
                db.session.rollback()
                return redirect(url_for("event_detail", event_id=event.id))
            event.status = "settlement"
            new_snapshot = event_audit_snapshot(event)
            audit_log(
                "event",
                "event_capture_finished",
                f"Strafenerfassung für Kegelabend vom {event.event_date} abgeschlossen",
                details=audit_diff_lines(old_snapshot, new_snapshot),
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            db.session.commit()
            flash("Bitte jetzt Barzahlungen erfassen. Danach werden die Bahnkosten eingetragen.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "reopen":
            if not can_edit_closed_events():
                flash("Nur Admin oder Kassierer/-in dürfen abgeschlossene oder ausgefallene Abende wieder öffnen.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            old_snapshot = event_audit_snapshot(event)
            event.status = "open"
            new_snapshot = event_audit_snapshot(event)
            audit_log(
                "event",
                "event_reopened",
                f"Kegelabend vom {event.event_date} wieder geöffnet",
                details=audit_diff_lines(old_snapshot, new_snapshot),
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            db.session.commit()
            flash("Kegelabend wurde wieder geöffnet. Bereits erfasste Zahlungen bleiben als Vorschlag erhalten und werden beim erneuten Verbuchen sauber neu gebucht.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "save_settlement":
            old_snapshot = event_audit_snapshot(event)
            settlement_detail_lines = []
            if event.status != "settlement":
                flash("Barzahlungen können nur im Abrechnungs-Schritt erfasst werden.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            block_reason = closed_year_block_message(event.event_date, require_admin_confirmation=False)
            if block_reason:
                flash(block_reason, "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            reset_event_bookings(event)

            participants = EventParticipant.query.filter_by(event_id=event.id).all()
            average_present_cents = event_average_present_penalty_cents(event)
            marker = f"Kegelabend #{event.id} vom {event.event_date}"

            for participant in participants:
                penalty_cents = participant_penalty_cents(
                    participant,
                    event.event_date,
                    average_present_cents,
                )

                if participant.member_id and penalty_cents:
                    db.session.add(MemberPenaltyTransaction(
                        member_id=participant.member_id,
                        event_id=event.id,
                        participant_id=participant.id,
                        category="event_penalty",
                        amount_cents=penalty_cents,
                        booking_date=event.event_date,
                        description=f"Strafen {marker}",
                    ))

                try:
                    paid_cents = form_euro_to_cents(f"paid_{participant.id}", f"Bezahlt für {participant.name()}")
                except ValueError:
                    db.session.rollback()
                    return redirect(url_for("event_detail", event_id=event.id))

                if not participant.member_id and paid_cents != penalty_cents:
                    db.session.rollback()
                    flash(
                        f"Gast {participant.name()}: Der fällige Betrag von {cents_to_euro(penalty_cents)} € muss vollständig bar bezahlt werden (kein Strafkonto für Gäste).",
                        "danger",
                    )
                    return redirect(url_for("event_detail", event_id=event.id))

                guest_hint = "" if participant.member_id else " (Gastkegler)"
                settlement_detail_lines.append(f"{participant.name()}{guest_hint}: Strafen {cents_to_euro(penalty_cents)} €, bezahlt {cents_to_euro(paid_cents)} €")

                if paid_cents:
                    payment_transaction = None
                    if participant.member_id:
                        payment_transaction = MemberPenaltyTransaction(
                            member_id=participant.member_id,
                            event_id=event.id,
                            participant_id=participant.id,
                            category="cash_payment",
                            amount_cents=-paid_cents,
                            booking_date=event.event_date,
                            description=f"Barzahlung {marker}",
                        )
                        db.session.add(payment_transaction)
                        db.session.flush()

                    # Gastkegler im Kassenbuch kenntlich machen (kein Strafkonto, Gastgebühr
                    # plus ggf. Strafen). Kategorie und Notiz-Anfang bleiben unverändert,
                    # daran hängen Storno-Logik und erneutes Verbuchen.
                    is_guest = not participant.member_id
                    db.session.add(AccountTransaction(
                        account="cash",
                        category="event_cash_payment",
                        amount_cents=paid_cents,
                        booking_date=event.event_date,
                        description=f"Barzahlung {'Gastkegler ' if is_guest else ''}{participant.name()} - {marker}",
                    ))
                    db.session.add(CashbookEntry(
                        booking_date=event.event_date,
                        direction="income",
                        account="cash",
                        amount_cents=paid_cents,
                        category="Barzahlung Strafen",
                        person=f"{participant.name()} (Gastkegler)" if is_guest else participant.name(),
                        reason=(
                            f"Barzahlung Gastkegler (Gastgebühr/Strafen) Kegelabend {event.event_date}"
                            if is_guest else f"Barzahlung Strafen Kegelabend {event.event_date}"
                        ),
                        note=(
                            f"Automatisch aus Kegelabend-Abrechnung (Gastkegler, kein Strafkonto): {marker}"
                            if is_guest else f"Automatisch aus Kegelabend-Abrechnung: {marker}"
                        ),
                        penalty_transaction_id=payment_transaction.id if payment_transaction else None,
                        created_by_user_id=current_user.id,
                    ))

            event.status = "lane_cost"
            new_snapshot = event_audit_snapshot(event)
            detail_text = audit_diff_lines(old_snapshot, new_snapshot)
            if settlement_detail_lines:
                detail_text += "\nBarzahlungen:\n" + "\n".join(settlement_detail_lines)
            audit_log(
                "event",
                "event_settlement_saved",
                f"Barzahlungen für Kegelabend vom {event.event_date} verbucht",
                details=detail_text,
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            db.session.commit()
            flash("Barzahlungen wurden verbucht. Jetzt können die Bahnkosten eingetragen werden.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "final_close":
            old_snapshot = event_audit_snapshot(event)
            if event.status != "lane_cost":
                flash("Bitte zuerst die Barzahlungen verbuchen. Danach werden die Bahnkosten erfasst.", "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            block_reason = closed_year_block_message(event.event_date, require_admin_confirmation=False)
            if block_reason:
                flash(block_reason, "danger")
                return redirect(url_for("event_detail", event_id=event.id))

            try:
                event.lane_cost_cents = form_euro_to_cents("lane_cost", "Bahnkosten")
            except ValueError:
                db.session.rollback()
                return redirect(url_for("event_detail", event_id=event.id))

            marker = f"Kegelabend #{event.id} vom {event.event_date}"

            lane_cashbook_entry = None
            if event.lane_cost_cents:
                lane_cashbook_entry = CashbookEntry(
                    booking_date=event.event_date,
                    direction="expense",
                    account="cash",
                    amount_cents=event.lane_cost_cents,
                    category="Bahnkosten",
                    person="Kegelbahn",
                    reason=f"Bahnkosten Kegelabend {event.event_date}",
                    note=f"Automatisch beim Abschluss gebucht: {marker}",
                    created_by_user_id=current_user.id,
                )
                db.session.add(lane_cashbook_entry)
                db.session.flush()
                db.session.add(AccountTransaction(
                    account="cash",
                    category="lane_cost",
                    amount_cents=-event.lane_cost_cents,
                    booking_date=event.event_date,
                    description=f"Bahnkosten {marker}",
                ))

            receipt_file = request.files.get("lane_receipt_file")
            if receipt_file and receipt_file.filename:
                if document_allowed(receipt_file.filename):
                    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
                    original_filename = secure_filename(receipt_file.filename)
                    suffix = original_filename.rsplit(".", 1)[1].lower() if "." in original_filename else "bin"
                    stored_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(8)}.{suffix}"
                    target = DOCUMENT_DIR / stored_filename
                    receipt_file.save(target)
                    document = Document(
                        title=f"Kegelbahn-Rechnung {event.event_date.strftime('%d.%m.%Y')}",
                        category="Kegelbahn-Rechnung",
                        document_date=event.event_date,
                        description=f"Beim Abschluss des Kegelabends hochgeladener Bahnkosten-Beleg über {cents_to_euro(event.lane_cost_cents)} €.",
                        original_filename=original_filename,
                        stored_filename=stored_filename,
                        mime_type=receipt_file.mimetype,
                        file_size=target.stat().st_size if target.exists() else 0,
                        event_id=event.id,
                        cashbook_entry_id=lane_cashbook_entry.id if lane_cashbook_entry else None,
                        uploaded_by_user_id=current_user.id,
                    )
                    db.session.add(document)
                else:
                    flash("Bahnkosten wurden gespeichert, aber der Beleg-Dateityp ist nicht erlaubt.", "warning")

            event.status = "closed"
            new_snapshot = event_audit_snapshot(event)
            audit_log(
                "event",
                "event_closed",
                f"Kegelabend vom {event.event_date} endgültig abgeschlossen",
                details=audit_diff_lines(old_snapshot, new_snapshot),
                object_type="BowlingEvent",
                object_id=event.id,
                old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
                new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
            )
            release_event_lock(event)
            db.session.commit()
            flash(f"Kegelabend wurde abgeschlossen. Barkasse jetzt: {cents_to_euro(account_balance('cash'))} €", "success")
            if setting_value("event_closed_confirmation_enabled", "0") == "1":
                return redirect(url_for("event_detail", event_id=event.id))
            return redirect(url_for("events"))

    participants = EventParticipant.query.filter_by(event_id=event.id).all()
    penalty_types = active_penalty_types()
    participant_rows = []

    average_present_raw_cents = event_average_present_penalty_raw_cents(event)
    average_present_cents = event_average_present_penalty_cents(event)
    guest_fee_cents = current_rate_cents("guest_fee", event.event_date)
    absence_excused_cents = current_rate_cents("absence_excused", event.event_date)
    absence_unexcused_cents = current_rate_cents("absence_unexcused", event.event_date)
    lane_cost_default_cents = current_rate_cents("lane_cost_default", event.event_date)

    paid_by_participant = {}
    payment_transactions = MemberPenaltyTransaction.query.filter_by(event_id=event.id, category="cash_payment").all()
    for transaction in payment_transactions:
        if transaction.participant_id:
            paid_by_participant[transaction.participant_id] = paid_by_participant.get(transaction.participant_id, 0) + abs(transaction.amount_cents or 0)

    for participant in participants:
        penalty_values = {}
        for penalty_type in penalty_types:
            penalty = get_or_create_participant_penalty(participant, penalty_type)
            penalty_values[penalty_type.id] = penalty

        penalty_cents = participant_penalty_cents(
            participant,
            event.event_date,
            average_present_cents,
        )
        previous_balance_cents = member_penalty_balance(participant.member_id) if participant.member_id else 0
        # Während der Erfassung/Abrechnung sind die aktuellen Strafen noch nicht endgültig gebucht,
        # deshalb werden sie zum bisherigen Strafkonto addiert. Nach den Barzahlungen bzw. nach
        # endgültigem Abschluss enthält member_penalty_balance() bereits Strafen minus Zahlungen.
        if participant.member_id:
            if event.status in ("lane_cost", "closed"):
                balance_cents = previous_balance_cents
            else:
                balance_cents = previous_balance_cents + penalty_cents
        elif event.status in ("lane_cost", "closed"):
            # Gäste haben kein Strafkonto: der Abendbetrag muss sofort bar
            # beglichen werden, danach bleibt kein Saldo offen.
            balance_cents = 0
        else:
            balance_cents = penalty_cents
        participant_rows.append({
            "participant": participant,
            "display_name": participant_display_name(participant),
            "penalty_values": penalty_values,
            "penalty_cents": penalty_cents,
            "penalty_euro": cents_to_euro(penalty_cents),
            "previous_balance_cents": previous_balance_cents if participant.member_id else 0,
            "previous_balance_euro": cents_to_euro(previous_balance_cents) if participant.member_id else "0,00",
            "balance_cents": balance_cents,
            "balance_euro": cents_to_euro(balance_cents),
            "paid_cents": paid_by_participant.get(participant.id, 0),
            "paid_euro": cents_to_euro(paid_by_participant.get(participant.id, 0)),
        })

    participant_rows.sort(key=participant_sort_key)
    settlement_rows = sorted(participant_rows, key=lambda row: (participant_sort_name(row["participant"]), row["display_name"].casefold()))

    db.session.commit()

    return render_template(
        "event_detail.html",
        event=event,
        participants=participants,
        participant_rows=participant_rows,
        settlement_rows=settlement_rows,
        penalty_types=penalty_types,
        can_edit_closed=can_edit_closed_events(),
        can_override_lock=can_override_event_lock(),
        lock_allowed=lock_allowed,
        active_lock=active_lock,
        cents_to_euro=cents_to_euro,
        average_present_raw_cents=average_present_raw_cents,
        average_present_raw_euro=cents_to_euro(average_present_raw_cents),
        average_present_cents=average_present_cents,
        average_present_euro=cents_to_euro(average_present_cents),
        average_rounding_text=average_rounding_label(average_present_raw_cents, average_present_cents),
        guest_fee_cents=guest_fee_cents,
        guest_fee_euro=cents_to_euro(guest_fee_cents),
        absence_excused_cents=absence_excused_cents,
        absence_excused_euro=cents_to_euro(absence_excused_cents),
        absence_unexcused_cents=absence_unexcused_cents,
        absence_unexcused_euro=cents_to_euro(absence_unexcused_cents),
        lane_cost_default_cents=lane_cost_default_cents,
        lane_cost_default_euro=cents_to_euro(lane_cost_default_cents),
        lane_cost_prefill_euro=cents_to_euro(event.lane_cost_cents or lane_cost_default_cents),
        current_cash_balance_cents=account_balance("cash"),
        current_cash_balance_euro=cents_to_euro(account_balance("cash")),
        allow_guest_add=(event.status == "open" and not event_has_started_entries(event)),
    )


@app.route("/events/<int:event_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def event_delete(event_id):
    event = BowlingEvent.query.get_or_404(event_id)
    reset_event_bookings(event)

    participants = EventParticipant.query.filter_by(event_id=event.id).all()
    for participant in participants:
        ParticipantPenalty.query.filter_by(participant_id=participant.id).delete()
        db.session.delete(participant)

    EventEditLock.query.filter_by(event_id=event.id).delete()
    event_date = event.event_date
    db.session.delete(event)
    audit_log(
        "event",
        "event_deleted",
        f"Kegelabend vom {event_date} gelöscht",
        object_type="BowlingEvent",
        object_id=event_id,
    )
    db.session.commit()

    flash("Kegelabend wurde gelöscht.", "success")
    return redirect(url_for("events"))
