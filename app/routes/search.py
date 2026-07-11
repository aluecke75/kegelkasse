"""Globale Suche über Mitglieder, Kegelabende, Kassenbuch, Dokumente,
Strafarten und Revisionsprotokoll."""
from flask import request, render_template
from flask_login import login_required, current_user
from sqlalchemy import or_

from extensions import app
from auth import role_required
from models import db, Member, BowlingEvent, CashbookEntry, Document, PenaltyType, AuditLog


def parse_partial_date_query(q):
    """Erkennt Tagesangaben wie '3.5', '03.05.', '03.05.2026' in der Sucheingabe."""
    q = q.strip().rstrip(".")
    parts = q.split(".")
    try:
        if len(parts) == 2 and all(parts):
            day, month = int(parts[0]), int(parts[1])
            if 1 <= day <= 31 and 1 <= month <= 12:
                return day, month, None
        if len(parts) == 3 and parts[0] and parts[1]:
            day, month = int(parts[0]), int(parts[1])
            year = int(parts[2]) if parts[2] else None
            if 1 <= day <= 31 and 1 <= month <= 12:
                return day, month, year
    except ValueError:
        return None
    return None


@app.route("/search")
@login_required
@role_required("admin", "cashier", "auditor")
def search_page():
    q = (request.args.get("q") or "").strip()
    results = {"members": [], "events": [], "cashbook": [], "documents": [], "penalty_types": [], "audit_log": []}
    total = 0

    if q:
        like = f"%{q}%"

        results["members"] = Member.query.filter(
            or_(
                Member.first_name.ilike(like),
                Member.last_name.ilike(like),
                Member.nickname.ilike(like),
                Member.email.ilike(like),
            )
        ).order_by(Member.first_name.asc()).limit(20).all()

        date_guess = parse_partial_date_query(q)
        if date_guess:
            day, month, year = date_guess
            event_query = BowlingEvent.query.filter(
                db.extract("day", BowlingEvent.event_date) == day,
                db.extract("month", BowlingEvent.event_date) == month,
            )
            if year:
                event_query = event_query.filter(db.extract("year", BowlingEvent.event_date) == year)
            results["events"] = event_query.order_by(BowlingEvent.event_date.desc()).limit(20).all()
        else:
            results["events"] = BowlingEvent.query.filter(
                BowlingEvent.note.ilike(like)
            ).order_by(BowlingEvent.event_date.desc()).limit(20).all()

        results["cashbook"] = CashbookEntry.query.filter(
            or_(
                CashbookEntry.reason.ilike(like),
                CashbookEntry.person.ilike(like),
                CashbookEntry.note.ilike(like),
                CashbookEntry.category.ilike(like),
            )
        ).order_by(CashbookEntry.booking_date.desc()).limit(20).all()

        results["documents"] = Document.query.filter(
            Document.deleted_at.is_(None),
            or_(
                Document.title.ilike(like),
                Document.description.ilike(like),
                Document.original_filename.ilike(like),
            ),
        ).order_by(Document.uploaded_at.desc()).limit(20).all()

        results["penalty_types"] = PenaltyType.query.filter(PenaltyType.name.ilike(like)).limit(20).all()

        if current_user.role in ("admin", "cashier", "auditor"):
            results["audit_log"] = AuditLog.query.filter(
                or_(
                    AuditLog.title.ilike(like),
                    AuditLog.details.ilike(like),
                    AuditLog.old_value.ilike(like),
                    AuditLog.new_value.ilike(like),
                )
            ).order_by(AuditLog.created_at.desc()).limit(20).all()

        total = sum(len(v) for v in results.values())

    return render_template("search.html", q=q, results=results, total=total)
