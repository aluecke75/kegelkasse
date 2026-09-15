"""Mitgliederverwaltung: Liste, Anlegen, Bearbeiten, Löschen, Login-Konto pflegen."""
from datetime import datetime

from flask import request, flash, redirect, url_for, render_template
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash

from extensions import app
from auth import role_required
from models import db, Member, User
from services.audit import audit_log, audit_value, audit_diff_lines, member_audit_snapshot
from services.validation import validate_email_format

ASSIGNABLE_MEMBER_ROLES = ("member", "cashier", "admin")


def update_member_login(member):
    create_login = request.form.get("create_login") == "on"
    username = request.form.get("username", "").strip()
    role = request.form.get("role", "member").strip()
    password = request.form.get("password", "")

    if role not in ASSIGNABLE_MEMBER_ROLES:
        role = "member"

    # Nur ein Admin darf die Admin-Rolle vergeben - sonst könnte sich ein
    # Kassierer-Konto über dieses ganz normale Formular selbst (oder ein
    # anderes Mitglied) zum Admin machen.
    if role == "admin" and current_user.role != "admin":
        flash("Nur Admins dürfen die Rolle 'Admin' vergeben.", "danger")
        return

    if not create_login:
        if member.user:
            member.user.active = False
        return

    if not username:
        flash("Für ein Login-Konto muss ein Benutzername angegeben werden.", "danger")
        return

    if member.user:
        user = member.user
        if username != user.username:
            existing = User.query.filter_by(username=username).first()
            if existing:
                flash("Dieser Benutzername ist bereits vergeben.", "danger")
                return
        user.username = username
        user.role = role
        user.active = True

        if password:
            user.password_hash = generate_password_hash(password)
    else:
        if not password:
            flash("Für ein neues Login-Konto muss ein Passwort gesetzt werden.", "danger")
            return

        existing = User.query.filter_by(username=username).first()
        if existing:
            flash("Dieser Benutzername ist bereits vergeben.", "danger")
            return

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            role=role,
            active=True,
        )

        db.session.add(user)
        db.session.flush()
        member.user_id = user.id


@app.route("/members")
@login_required
def members():
    members_list = Member.query.order_by(
        Member.active.desc(),
        Member.first_name,
        Member.last_name,
    ).all()

    return render_template("members.html", members=members_list)


@app.route("/members/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier")
def member_new():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        nickname = request.form.get("nickname", "").strip()
        email = request.form.get("email", "").strip()
        joined_at_raw = request.form.get("joined_at", "").strip()
        note = request.form.get("note", "").strip()

        if not first_name:
            flash("Vorname ist ein Pflichtfeld.", "danger")
            return redirect(url_for("member_new"))

        if not validate_email_format(email):
            flash("Bitte eine gültige E-Mail-Adresse eingeben.", "danger")
            return redirect(url_for("member_new"))

        joined_at = (
            datetime.strptime(joined_at_raw, "%Y-%m-%d").date()
            if joined_at_raw
            else datetime.today().date()
        )

        member = Member(
            first_name=first_name,
            last_name=last_name,
            nickname=nickname or None,
            email=email or None,
            joined_at=joined_at,
            note=note or None,
            active=True,
        )

        db.session.add(member)
        db.session.flush()

        update_member_login(member)

        audit_log(
            "member",
            "member_created",
            f"Mitglied {member.display_name()} angelegt",
            details="Neues Mitglied angelegt.",
            object_type="Member",
            object_id=member.id,
            new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in member_audit_snapshot(member).items()),
        )
        db.session.commit()

        flash("Mitglied wurde angelegt.", "success")
        return redirect(url_for("members"))

    return render_template(
        "member_form.html",
        member=None,
        today=datetime.today().date().isoformat(),
    )


@app.route("/members/<int:member_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier")
def member_edit(member_id):
    member = Member.query.get_or_404(member_id)

    if request.method == "POST":
        email_input = request.form.get("email", "").strip()
        if not validate_email_format(email_input):
            flash("Bitte eine gültige E-Mail-Adresse eingeben.", "danger")
            return redirect(url_for("member_edit", member_id=member.id))

        old_snapshot = member_audit_snapshot(member)

        member.first_name = request.form.get("first_name", "").strip()
        member.last_name = request.form.get("last_name", "").strip()
        member.nickname = request.form.get("nickname", "").strip() or None
        member.email = email_input or None
        member.note = request.form.get("note", "").strip() or None
        member.active = request.form.get("active") == "on"

        joined_at_raw = request.form.get("joined_at", "").strip()
        left_at_raw = request.form.get("left_at", "").strip()

        member.joined_at = (
            datetime.strptime(joined_at_raw, "%Y-%m-%d").date()
            if joined_at_raw
            else None
        )

        member.left_at = (
            datetime.strptime(left_at_raw, "%Y-%m-%d").date()
            if left_at_raw
            else None
        )

        update_member_login(member)

        new_snapshot = member_audit_snapshot(member)
        audit_log(
            "member",
            "member_saved",
            f"Mitglied {member.display_name()} gespeichert",
            details=audit_diff_lines(old_snapshot, new_snapshot),
            object_type="Member",
            object_id=member.id,
            old_value="\n".join(f"{key}: {audit_value(value)}" for key, value in old_snapshot.items()),
            new_value="\n".join(f"{key}: {audit_value(value)}" for key, value in new_snapshot.items()),
        )
        db.session.commit()

        flash("Mitglied wurde gespeichert.", "success")
        return redirect(url_for("members"))

    return render_template(
        "member_form.html",
        member=member,
        today=datetime.today().date().isoformat(),
    )


@app.route("/members/<int:member_id>/delete", methods=["POST"])
@login_required
@role_required("admin", "cashier")
def member_delete(member_id):
    member = Member.query.get_or_404(member_id)

    if member.user:
        db.session.delete(member.user)

    member_name = member.display_name()
    db.session.delete(member)
    audit_log(
        "member",
        "member_deleted",
        f"Mitglied {member_name} gelöscht",
        details=f"Mitglied gelöscht: {member_name}",
        object_type="Member",
        object_id=member_id,
    )
    db.session.commit()

    flash("Mitglied wurde gelöscht.", "success")
    return redirect(url_for("members"))
