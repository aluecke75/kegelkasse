from datetime import datetime, date
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(20), nullable=False, default="viewer")
    active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def role_label(self):
        labels = {
            "admin": "Admin",
            "cashier": "Kassierer/-in",
            "viewer": "Mitglied – nur lesen",
        }
        return labels.get(self.role, self.role)


class Member(db.Model):
    __tablename__ = "members"

    id = db.Column(db.Integer, primary_key=True)

    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    nickname = db.Column(db.String(80), nullable=True)

    email = db.Column(db.String(255), nullable=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    user = db.relationship("User", backref="member_profile", uselist=False)

    active = db.Column(db.Boolean, default=True)

    joined_at = db.Column(db.Date, default=date.today)
    left_at = db.Column(db.Date, nullable=True)

    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def display_name(self):
        if self.nickname:
            return f"{self.first_name} „{self.nickname}“ {self.last_name}"
        return f"{self.first_name} {self.last_name}"


class RateSetting(db.Model):
    __tablename__ = "rate_settings"

    id = db.Column(db.Integer, primary_key=True)

    key = db.Column(db.String(80), nullable=False)
    label = db.Column(db.String(120), nullable=False)

    amount_cents = db.Column(db.Integer, nullable=False)
    valid_from = db.Column(db.Date, nullable=False)

    active = db.Column(db.Boolean, default=True)
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def amount_euro(self):
        return f"{self.amount_cents / 100:.2f}".replace(".", ",")


class AccountTransaction(db.Model):
    __tablename__ = "account_transactions"

    id = db.Column(db.Integer, primary_key=True)

    account = db.Column(db.String(20), nullable=False)
    category = db.Column(db.String(50), nullable=False)

    amount_cents = db.Column(db.Integer, nullable=False)
    booking_date = db.Column(db.Date, nullable=False)

    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def amount_euro(self):
        return f"{self.amount_cents / 100:.2f}".replace(".", ",")


class BowlingEvent(db.Model):
    __tablename__ = "bowling_events"

    id = db.Column(db.Integer, primary_key=True)

    event_date = db.Column(db.Date, nullable=False)
    lane_cost_cents = db.Column(db.Integer, nullable=False, default=0)

    status = db.Column(db.String(20), nullable=False, default="open")
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def lane_cost_euro(self):
        return f"{self.lane_cost_cents / 100:.2f}".replace(".", ",")
        
class EventParticipant(db.Model):
    __tablename__ = "event_participants"

    id = db.Column(db.Integer, primary_key=True)

    event_id = db.Column(db.Integer, db.ForeignKey("bowling_events.id"), nullable=False)
    event = db.relationship("BowlingEvent", backref="participants")

    member_id = db.Column(db.Integer, db.ForeignKey("members.id"), nullable=True)
    member = db.relationship("Member")

    guest_name = db.Column(db.String(120), nullable=True)

    status = db.Column(db.String(30), nullable=False, default="present")
    # present, excused, unexcused, guest

    pumps = db.Column(db.Integer, default=0)
    wreaths = db.Column(db.Integer, default=0)
    all_nines = db.Column(db.Integer, default=0)
    lost_games = db.Column(db.Integer, default=0)

    manual_penalty_cents = db.Column(db.Integer, default=0)
    manual_penalty_note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def name(self):
        if self.member:
            return self.member.display_name()
        return self.guest_name or "Gast"

    def manual_penalty_euro(self):
        return f"{self.manual_penalty_cents / 100:.2f}".replace(".", ",")