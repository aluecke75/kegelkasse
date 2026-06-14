from datetime import datetime

from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import check_password_hash, generate_password_hash

from auth import create_initial_admin, role_required
from config import Config
from models import (
    db,
    User,
    Member,
    RateSetting,
    AccountTransaction,
    BowlingEvent,
    EventParticipant,
)


app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Bitte zuerst anmelden."
login_manager.init_app(app)


RATE_TYPES = {
    "monthly_fee": "Monatsbeitrag",
    "guest_fee": "Gastgebühr",
    "absence_excused": "Fehlen entschuldigt",
    "absence_unexcused": "Fehlen unentschuldigt",
    "lane_cost_default": "Bahnkosten Standard",
    "penalty_pump": "Pumpe",
    "penalty_wreath": "Kranz",
    "penalty_all_nine": "Alle Neune",
    "penalty_lost_game": "Verlorenes Spiel",
}


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def euro_to_cents(value):
    cleaned = value.strip().replace("€", "").replace(",", ".")
    return int(round(float(cleaned) * 100))


def cents_to_euro(value):
    return f"{value / 100:.2f}".replace(".", ",")


def account_balance(account):
    transactions = AccountTransaction.query.filter_by(account=account).all()
    return sum(transaction.amount_cents for transaction in transactions)


def current_rate_cents(key, target_date):
    rate = (
        RateSetting.query
        .filter(RateSetting.key == key)
        .filter(RateSetting.valid_from <= target_date)
        .order_by(RateSetting.valid_from.desc())
        .first()
    )

    return rate.amount_cents if rate else 0


def create_default_rates():
    defaults = {
        "monthly_fee": 2000,
        "guest_fee": 500,
        "absence_excused": 250,
        "absence_unexcused": 250,
        "lane_cost_default": 1850,
        "penalty_pump": 20,
        "penalty_wreath": 20,
        "penalty_all_nine": 20,
        "penalty_lost_game": 20,
    }

    default_date = datetime.today().date()

    for key, amount in defaults.items():
        exists = RateSetting.query.filter_by(key=key).first()

        if not exists:
            rate = RateSetting(
                key=key,
                label=RATE_TYPES[key],
                amount_cents=amount,
                valid_from=default_date,
                active=True,
                note="Automatisch angelegter Startwert",
            )
            db.session.add(rate)

    db.session.commit()


def update_member_login(member):
    create_login = request.form.get("create_login") == "on"
    username = request.form.get("username", "").strip()
    role = request.form.get("role", "viewer").strip()
    password = request.form.get("password", "")

    if not create_login:
        if member.user:
            member.user.active = False
        return

    if not username:
        flash("Für ein Login-Konto muss ein Benutzername angegeben werden.", "danger")
        return

    if member.user:
        user = member.user
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


@app.route("/")
@login_required
def dashboard():
    member_count = Member.query.count()
    active_member_count = Member.query.filter_by(active=True).count()

    return render_template(
        "dashboard.html",
        member_count=member_count,
        active_member_count=active_member_count,
        cash_balance=cents_to_euro(account_balance("cash")),
        bank_balance=cents_to_euro(account_balance("bank")),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username, active=True).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for("dashboard"))

        flash("Benutzername oder Passwort ist falsch.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/admin")
@login_required
@role_required("admin")
def admin_area():
    users = User.query.order_by(User.username).all()
    return render_template("admin.html", users=users)


@app.route("/members")
@login_required
def members():
    members_list = Member.query.order_by(
        Member.active.desc(),
        Member.last_name,
        Member.first_name,
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

        if not first_name or not last_name:
            flash("Vorname und Nachname sind Pflichtfelder.", "danger")
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
        member.first_name = request.form.get("first_name", "").strip()
        member.last_name = request.form.get("last_name", "").strip()
        member.nickname = request.form.get("nickname", "").strip() or None
        member.email = request.form.get("email", "").strip() or None
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

    db.session.delete(member)
    db.session.commit()

    flash("Mitglied wurde gelöscht.", "success")
    return redirect(url_for("members"))


@app.route("/settings/rates")
@login_required
def rate_settings():
    rates = RateSetting.query.order_by(
        RateSetting.key,
        RateSetting.valid_from.desc(),
    ).all()

    return render_template("rate_settings.html", rates=rates, rate_types=RATE_TYPES)


@app.route("/settings/rates/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def rate_new():
    if request.method == "POST":
        key = request.form.get("key", "").strip()
        amount = request.form.get("amount", "").strip()
        valid_from_raw = request.form.get("valid_from", "").strip()
        note = request.form.get("note", "").strip()

        if key not in RATE_TYPES:
            flash("Ungültiger Einstellungstyp.", "danger")
            return redirect(url_for("rate_new"))

        rate = RateSetting(
            key=key,
            label=RATE_TYPES[key],
            amount_cents=euro_to_cents(amount),
            valid_from=datetime.strptime(valid_from_raw, "%Y-%m-%d").date(),
            active=True,
            note=note or None,
        )

        db.session.add(rate)
        db.session.commit()

        flash("Neuer Wert wurde mit Gültigkeitsdatum angelegt.", "success")
        return redirect(url_for("rate_settings"))

    return render_template(
        "rate_form.html",
        rate_types=RATE_TYPES,
        today=datetime.today().date().isoformat(),
    )


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

        AccountTransaction.query.filter_by(category="opening_balance").delete()

        db.session.add(AccountTransaction(
            account="cash",
            category="opening_balance",
            amount_cents=euro_to_cents(cash_amount or "0"),
            booking_date=booking_date,
            description=description or "Startbestand Barkasse",
        ))

        db.session.add(AccountTransaction(
            account="bank",
            category="opening_balance",
            amount_cents=euro_to_cents(bank_amount or "0"),
            booking_date=booking_date,
            description=description or "Startbestand Bankkonto",
        ))

        db.session.commit()

        flash("Startbestände wurden gespeichert.", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "opening_balances.html",
        today=datetime.today().date().isoformat(),
        cash_balance=cents_to_euro(account_balance("cash")),
        bank_balance=cents_to_euro(account_balance("bank")),
    )


@app.route("/events")
@login_required
def events():
    event_list = BowlingEvent.query.order_by(BowlingEvent.event_date.desc()).all()
    return render_template("events.html", events=event_list)


@app.route("/events/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier")
def event_new():
    today = datetime.today().date()

    if request.method == "POST":
        event_date_raw = request.form.get("event_date", "").strip()
        lane_cost_raw = request.form.get("lane_cost", "").strip()
        note = request.form.get("note", "").strip()

        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()

        event = BowlingEvent(
            event_date=event_date,
            lane_cost_cents=euro_to_cents(lane_cost_raw or "0"),
            status="open",
            note=note or None,
        )

        db.session.add(event)
        db.session.commit()

        flash("Kegelabend wurde angelegt.", "success")
        return redirect(url_for("events"))

    default_lane_cost = current_rate_cents("lane_cost_default", today)

    return render_template(
        "event_form.html",
        event=None,
        today=today.isoformat(),
        lane_cost=cents_to_euro(default_lane_cost),
    )

@app.route("/events/<int:event_id>", methods=["GET", "POST"])
@login_required
def event_detail(event_id):
    event = BowlingEvent.query.get_or_404(event_id)

    existing_count = EventParticipant.query.filter_by(event_id=event.id).count()

    if existing_count == 0:
        active_members = Member.query.filter_by(active=True).order_by(Member.last_name, Member.first_name).all()

        for member in active_members:
            participant = EventParticipant(
                event_id=event.id,
                member_id=member.id,
                status="present",
            )
            db.session.add(participant)

        db.session.commit()

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "save":
            participants = EventParticipant.query.filter_by(event_id=event.id).all()

            for participant in participants:
                prefix = f"participant_{participant.id}_"

                participant.status = request.form.get(prefix + "status", participant.status)
                participant.pumps = int(request.form.get(prefix + "pumps", 0) or 0)
                participant.wreaths = int(request.form.get(prefix + "wreaths", 0) or 0)
                participant.all_nines = int(request.form.get(prefix + "all_nines", 0) or 0)
                participant.lost_games = int(request.form.get(prefix + "lost_games", 0) or 0)

                manual_amount = request.form.get(prefix + "manual_penalty", "0").strip()
                participant.manual_penalty_cents = euro_to_cents(manual_amount or "0")
                participant.manual_penalty_note = request.form.get(prefix + "manual_note", "").strip() or None

            db.session.commit()
            flash("Kegelabend wurde gespeichert.", "success")
            return redirect(url_for("event_detail", event_id=event.id))

        if action == "add_guest":
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

    participants = EventParticipant.query.filter_by(event_id=event.id).all()

    return render_template(
        "event_detail.html",
        event=event,
        participants=participants,
    )

with app.app_context():
    db.create_all()

    create_initial_admin(
        app.config["ADMIN_USERNAME"],
        app.config["ADMIN_PASSWORD"],
    )

    create_default_rates()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)