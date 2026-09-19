from datetime import datetime, date
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=True)

    role = db.Column(db.String(20), nullable=False, default="member")
    active = db.Column(db.Boolean, default=True)
    # Systembenutzer sind reine Login-Konten ohne Kegelmitgliedschaft.
    # Sie erscheinen nicht in Kegelabenden, Monatsbeiträgen, Strafkonten oder Statistiken.
    is_system_user = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def role_label(self):
        labels = {
            "admin": "Admin",
            "cashier": "Kassierer/-in",
            "member": "Mitglied",
            "viewer": "Mitglied",
            "auditor": "Kassenprüfer/-in",
        }
        return labels.get(self.role, self.role)


class Member(db.Model):
    __tablename__ = "members"

    id = db.Column(db.Integer, primary_key=True)

    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=True)
    nickname = db.Column(db.String(80), nullable=True)

    email = db.Column(db.String(255), nullable=True)

    monthly_value_day = db.Column(db.Integer, nullable=True)
    # Geplanter Dauerauftragstag pro Mitglied, z. B. 7.
    # Wochenendverschiebungen auf Montag ändern diesen Basistag nicht.

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    user = db.relationship("User", backref="member_profile", uselist=False)

    active = db.Column(db.Boolean, default=True)

    joined_at = db.Column(db.Date, default=date.today)
    left_at = db.Column(db.Date, nullable=True)

    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def display_name(self):
        last = self.last_name or ""
        if self.nickname:
            return f"{self.first_name} „{self.nickname}“ {last}".strip()
        return f"{self.first_name} {last}".strip()

    def short_name(self):
        if self.nickname:
            return self.nickname
        return self.first_name


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
        return f"{self.amount_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    # Text statt String(255): manche Werte (z. B. PEM-öffentliche Schlüssel für
    # die Backup-Verschlüsselung) sind länger als 255 Zeichen. SQLite erzwingt
    # die Länge ohnehin nicht (Typ-Affinität statt harter Prüfung), aber die
    # Deklaration soll auch für eine spätere MariaDB/PostgreSQL-Migration korrekt sein.
    value = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    user = db.relationship("User")
    username = db.Column(db.String(80), nullable=True)

    category = db.Column(db.String(50), nullable=False)
    action = db.Column(db.String(80), nullable=False)
    object_type = db.Column(db.String(80), nullable=True)
    object_id = db.Column(db.Integer, nullable=True)

    title = db.Column(db.String(255), nullable=False)
    details = db.Column(db.Text, nullable=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)

    def category_label(self):
        labels = {
            "finance": "Finanzen",
            "event": "Kegelabend",
            "member": "Mitglieder",
            "settings": "Einstellungen",
            "penalty_type": "Strafarten",
            "system": "System",
            "annual_closing": "Jahresabschluss",
        }
        return labels.get(self.category, self.category)


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
        return f"{self.amount_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class CashbookEntry(db.Model):
    __tablename__ = "cashbook_entries"

    id = db.Column(db.Integer, primary_key=True)

    booking_date = db.Column(db.Date, nullable=False)
    direction = db.Column(db.String(20), nullable=False)
    # income = Einnahme, expense = Ausgabe

    account = db.Column(db.String(20), nullable=False)
    # cash = Barkasse, bank = Bank

    amount_cents = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(80), nullable=False)
    person = db.Column(db.String(160), nullable=True)
    reason = db.Column(db.String(255), nullable=False)
    note = db.Column(db.Text, nullable=True)

    # Herkunft aus dem CSV-Kontoauszug-Import (siehe services/csv_import_matching.py
    # und routes/monthly_contributions.py, Route monthly_bank_closing_csv_import).
    # Zusammen eindeutig: welche Zeile welches Dokuments bereits als Buchung
    # übernommen wurde - rein technische Dublettenprüfung, unabhängig vom
    # weichen Monatsbeitrags-Dubletten-Hinweis in der Review-Tabelle.
    source_document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=True)
    source_document = db.relationship("Document", foreign_keys=[source_document_id])
    source_row_index = db.Column(db.Integer, nullable=True)

    # Bei automatisch aus der Kegelabend-Abrechnung erzeugten Buchungen
    # "Barzahlung Strafen": ID der zugehörigen Strafkonto-Buchung
    # (MemberPenaltyTransaction, category "cash_payment"). Damit kann ein Storno
    # im Kassenbuch das Strafkonto des Mitglieds wieder zurückbuchen. Bewusst
    # ohne ForeignKey (die Strafkonto-Buchung kann bei einer erneuten Abrechnung
    # des Abends gelöscht werden); beim Storno wird deshalb zusätzlich geprüft,
    # ob die Zielbuchung noch zu diesem Eintrag passt. Ältere Buchungen: NULL.
    penalty_transaction_id = db.Column(db.Integer, nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    is_void = db.Column(db.Boolean, default=False)
    voided_at = db.Column(db.DateTime, nullable=True)
    voided_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    voided_by_user = db.relationship("User", foreign_keys=[voided_by_user_id])
    void_reason = db.Column(db.Text, nullable=True)

    def signed_amount_cents(self):
        amount = self.amount_cents or 0
        if self.direction == "transfer":
            return 0
        if self.direction == "expense":
            return -amount
        return amount

    def amount_euro(self):
        return f"{self.amount_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def signed_amount_euro(self):
        signed = self.signed_amount_cents() / 100
        return f"{signed:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def direction_label(self):
        if self.direction == "expense":
            return "Ausgabe"
        if self.direction == "transfer":
            return "Umbuchung"
        return "Einnahme"

    def account_label(self):
        labels = {"cash": "Barkasse", "bank": "Bank"}
        if self.direction == "transfer":
            source = labels.get(self.account, self.account)
            target = labels.get(self.person, self.person or "?")
            return f"{source} → {target}"
        return labels.get(self.account, self.account)


class CashAudit(db.Model):
    __tablename__ = "cash_audits"

    id = db.Column(db.Integer, primary_key=True)
    audit_date = db.Column(db.Date, nullable=False, default=date.today)

    expected_cash_cents = db.Column(db.Integer, nullable=False, default=0)
    counted_cash_cents = db.Column(db.Integer, nullable=False, default=0)
    difference_cash_cents = db.Column(db.Integer, nullable=False, default=0)

    expected_bank_cents = db.Column(db.Integer, nullable=False, default=0)
    statement_bank_cents = db.Column(db.Integer, nullable=False, default=0)
    difference_bank_cents = db.Column(db.Integer, nullable=False, default=0)

    note = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    confirmed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    confirmed_by_user = db.relationship("User", foreign_keys=[confirmed_by_user_id])
    confirmed_at = db.Column(db.DateTime, nullable=True)
    auditor_note = db.Column(db.Text, nullable=True)


    def euro(self, cents):
        return f"{(cents or 0) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def expected_cash_euro(self):
        return self.euro(self.expected_cash_cents)

    def counted_cash_euro(self):
        return self.euro(self.counted_cash_cents)

    def difference_cash_euro(self):
        return self.euro(self.difference_cash_cents)

    def expected_bank_euro(self):
        return self.euro(self.expected_bank_cents)

    def statement_bank_euro(self):
        return self.euro(self.statement_bank_cents)

    def difference_bank_euro(self):
        return self.euro(self.difference_bank_cents)

    def cash_status_label(self):
        if self.difference_cash_cents == 0:
            return "stimmt"
        return "Differenz"

    def bank_status_label(self):
        if self.difference_bank_cents == 0:
            return "stimmt"
        return "Differenz"

    def confirmation_status_label(self):
        return "bestätigt" if self.confirmed_at else "offen"


class AnnualClosing(db.Model):
    __tablename__ = "annual_closings"

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False, unique=True)
    closing_date = db.Column(db.Date, nullable=False, default=date.today)

    cash_balance_cents = db.Column(db.Integer, nullable=False, default=0)
    bank_balance_cents = db.Column(db.Integer, nullable=False, default=0)
    total_balance_cents = db.Column(db.Integer, nullable=False, default=0)

    open_penalties_cents = db.Column(db.Integer, nullable=False, default=0)
    member_credits_cents = db.Column(db.Integer, nullable=False, default=0)

    event_count = db.Column(db.Integer, nullable=False, default=0)
    cancelled_event_count = db.Column(db.Integer, nullable=False, default=0)
    open_event_count = db.Column(db.Integer, nullable=False, default=0)

    income_cents = db.Column(db.Integer, nullable=False, default=0)
    expense_cents = db.Column(db.Integer, nullable=False, default=0)

    last_cash_audit_id = db.Column(db.Integer, db.ForeignKey("cash_audits.id"), nullable=True)
    last_cash_audit = db.relationship("CashAudit")

    note = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    confirmed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    confirmed_by_user = db.relationship("User", foreign_keys=[confirmed_by_user_id])
    confirmed_at = db.Column(db.DateTime, nullable=True)
    auditor_note = db.Column(db.Text, nullable=True)

    def euro(self, cents):
        return f"{(cents or 0) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def cash_balance_euro(self):
        return self.euro(self.cash_balance_cents)

    def bank_balance_euro(self):
        return self.euro(self.bank_balance_cents)

    def total_balance_euro(self):
        return self.euro(self.total_balance_cents)

    def open_penalties_euro(self):
        return self.euro(self.open_penalties_cents)

    def member_credits_euro(self):
        return self.euro(self.member_credits_cents)

    def income_euro(self):
        return self.euro(self.income_cents)

    def expense_euro(self):
        return self.euro(self.expense_cents)


class InterestSetting(db.Model):
    __tablename__ = "interest_settings"

    id = db.Column(db.Integer, primary_key=True)
    annual_rate_basis_points = db.Column(db.Integer, nullable=False, default=0)
    # 125 = 1,25 % p. a.
    valid_from = db.Column(db.Date, nullable=False, default=date.today)
    payout_frequency = db.Column(db.String(20), nullable=False, default="yearly")
    # monthly, quarterly, half_yearly, yearly
    active = db.Column(db.Boolean, default=True)
    note = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    confirmed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    confirmed_by_user = db.relationship("User", foreign_keys=[confirmed_by_user_id])
    confirmed_at = db.Column(db.DateTime, nullable=True)
    auditor_note = db.Column(db.Text, nullable=True)

    def rate_percent(self):
        return f"{(self.annual_rate_basis_points or 0) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def frequency_label(self):
        labels = {
            "monthly": "monatlich",
            "quarterly": "vierteljährlich",
            "half_yearly": "halbjährlich",
            "yearly": "jährlich",
        }
        return labels.get(self.payout_frequency, self.payout_frequency or "-")


class InterestBooking(db.Model):
    __tablename__ = "interest_bookings"

    id = db.Column(db.Integer, primary_key=True)
    booking_date = db.Column(db.Date, nullable=False, default=date.today)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)

    setting_id = db.Column(db.Integer, db.ForeignKey("interest_settings.id"), nullable=True)
    setting = db.relationship("InterestSetting")

    basis_balance_cents = db.Column(db.Integer, nullable=False, default=0)
    expected_interest_cents = db.Column(db.Integer, nullable=False, default=0)
    actual_interest_cents = db.Column(db.Integer, nullable=False, default=0)
    capital_gains_tax_cents = db.Column(db.Integer, nullable=False, default=0)
    solidarity_tax_cents = db.Column(db.Integer, nullable=False, default=0)
    church_tax_cents = db.Column(db.Integer, nullable=False, default=0)
    tax_cents = db.Column(db.Integer, nullable=False, default=0)
    net_interest_cents = db.Column(db.Integer, nullable=False, default=0)

    cashbook_entry_id = db.Column(db.Integer, db.ForeignKey("cashbook_entries.id"), nullable=True)
    cashbook_entry = db.relationship("CashbookEntry", foreign_keys=[cashbook_entry_id])

    reversal_cashbook_entry_id = db.Column(db.Integer, db.ForeignKey("cashbook_entries.id"), nullable=True)
    reversal_cashbook_entry = db.relationship("CashbookEntry", foreign_keys=[reversal_cashbook_entry_id])

    is_cancelled = db.Column(db.Boolean, nullable=False, default=False)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancelled_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    cancelled_by_user = db.relationship("User", foreign_keys=[cancelled_by_user_id])
    cancel_reason = db.Column(db.Text, nullable=True)

    note = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    confirmed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    confirmed_by_user = db.relationship("User", foreign_keys=[confirmed_by_user_id])
    confirmed_at = db.Column(db.DateTime, nullable=True)
    auditor_note = db.Column(db.Text, nullable=True)

    def euro(self, cents):
        return f"{(cents or 0) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def basis_balance_euro(self):
        return self.euro(self.basis_balance_cents)

    def expected_interest_euro(self):
        return self.euro(self.expected_interest_cents)

    def actual_interest_euro(self):
        return self.euro(self.actual_interest_cents)

    def capital_gains_tax_euro(self):
        return self.euro(self.capital_gains_tax_cents)

    def solidarity_tax_euro(self):
        return self.euro(self.solidarity_tax_cents)

    def church_tax_euro(self):
        return self.euro(self.church_tax_cents)

    def tax_euro(self):
        total_tax = (
            (self.capital_gains_tax_cents or 0)
            + (self.solidarity_tax_cents or 0)
            + (self.church_tax_cents or 0)
        )
        if total_tax == 0:
            total_tax = self.tax_cents or 0
        return self.euro(total_tax)

    def net_interest_euro(self):
        if self.net_interest_cents:
            return self.euro(self.net_interest_cents)

        total_tax = (
            (self.capital_gains_tax_cents or 0)
            + (self.solidarity_tax_cents or 0)
            + (self.church_tax_cents or 0)
        )

        if total_tax == 0:
            total_tax = self.tax_cents or 0

        return self.euro((self.actual_interest_cents or 0) - total_tax)
def net_interest_euro(self):
    if self.net_interest_cents:
        return self.euro(self.net_interest_cents)
    total_tax = (
        (self.capital_gains_tax_cents or 0)
        + (self.solidarity_tax_cents or 0)
        + (self.church_tax_cents or 0)
    )
    if total_tax == 0:
        total_tax = self.tax_cents or 0
    return self.euro((self.actual_interest_cents or 0) - total_tax)
def capital_gains_tax_euro(self):
    return self.euro(self.capital_gains_tax_cents)

def solidarity_tax_euro(self):
    return self.euro(self.solidarity_tax_cents)

def church_tax_euro(self):
    return self.euro(self.church_tax_cents)

def tax_euro(self):
    total_tax = (
        (self.capital_gains_tax_cents or 0)
        + (self.solidarity_tax_cents or 0)
        + (self.church_tax_cents or 0)
    )
    if total_tax == 0:
        total_tax = self.tax_cents or 0
    return self.euro(total_tax)

def net_interest_euro(self):
    if self.net_interest_cents:
        return self.euro(self.net_interest_cents)
    total_tax = (
        (self.capital_gains_tax_cents or 0)
        + (self.solidarity_tax_cents or 0)
        + (self.church_tax_cents or 0)
        + (self.tax_cents or 0)
    )
    return self.euro((self.actual_interest_cents or 0) - total_tax)


class BowlingEvent(db.Model):
    __tablename__ = "bowling_events"

    id = db.Column(db.Integer, primary_key=True)

    event_date = db.Column(db.Date, nullable=False)
    lane_cost_cents = db.Column(db.Integer, nullable=False, default=0)

    status = db.Column(db.String(20), nullable=False, default="open")
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def lane_cost_euro(self):
        return f"{self.lane_cost_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        


class EventEditLock(db.Model):
    __tablename__ = "event_edit_locks"

    id = db.Column(db.Integer, primary_key=True)

    event_id = db.Column(db.Integer, db.ForeignKey("bowling_events.id"), nullable=False, unique=True)
    event = db.relationship("BowlingEvent", backref="edit_lock", uselist=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User")

    locked_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    locked_until = db.Column(db.DateTime, nullable=False)

    def is_expired(self):
        return self.locked_until < datetime.utcnow()


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
        return f"{self.manual_penalty_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class PenaltyType(db.Model):
    __tablename__ = "penalty_types"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), nullable=False)
    key = db.Column(db.String(80), unique=True, nullable=True)

    # count = Anzahl mit Plus/Minus, amount = freier Euro-Betrag
    kind = db.Column(db.String(20), nullable=False, default="count")
    amount_cents = db.Column(db.Integer, nullable=False, default=0)

    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=100)
    target_mode = db.Column(db.String(20), nullable=False, default="self")
    # self = Spieler zahlt selbst, others = alle anderen Anwesenden/Gäste zahlen

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def amount_euro(self):
        return f"{self.amount_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def kind_label(self):
        if self.kind == "amount":
            return "Betragsstrafe"
        return "Zählstrafe"

    def target_mode_label(self):
        if self.target_mode == "others":
            return "Alle anderen zahlen"
        return "Spieler zahlt selbst"


class ParticipantPenalty(db.Model):
    __tablename__ = "participant_penalties"

    id = db.Column(db.Integer, primary_key=True)

    participant_id = db.Column(db.Integer, db.ForeignKey("event_participants.id"), nullable=False)
    participant = db.relationship("EventParticipant", backref="dynamic_penalties")

    penalty_type_id = db.Column(db.Integer, db.ForeignKey("penalty_types.id"), nullable=False)
    penalty_type = db.relationship("PenaltyType")

    # Für count-Strafen: Anzahl. Für amount-Strafen: normalerweise 1.
    quantity = db.Column(db.Integer, nullable=False, default=0)

    # Nur für amount-Strafen relevant. Bei count-Strafen bleibt 0.
    amount_cents = db.Column(db.Integer, nullable=False, default=0)

    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def amount_euro(self):
        return f"{self.amount_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class MemberPenaltyTransaction(db.Model):
    __tablename__ = "member_penalty_transactions"

    id = db.Column(db.Integer, primary_key=True)

    member_id = db.Column(db.Integer, db.ForeignKey("members.id"), nullable=False)
    member = db.relationship("Member")

    event_id = db.Column(db.Integer, db.ForeignKey("bowling_events.id"), nullable=True)
    event = db.relationship("BowlingEvent")

    participant_id = db.Column(db.Integer, db.ForeignKey("event_participants.id"), nullable=True)
    participant = db.relationship("EventParticipant")

    category = db.Column(db.String(50), nullable=False)
    # event_penalty = offene Strafe aus Kegelabend
    # cash_payment = Barzahlung am Kegelabend
    # bank_transfer = spätere Überweisung
    # cash_payment_void = Storno einer Barzahlung über das Kassenbuch (positiv)
    # adjustment = manuelle Korrektur

    # Positiv = offen / Forderung
    # Negativ = Zahlung / Guthaben
    amount_cents = db.Column(db.Integer, nullable=False)

    booking_date = db.Column(db.Date, nullable=False)
    description = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def amount_euro(self):
        return f"{self.amount_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class MonthlyContributionBatch(db.Model):
    __tablename__ = "monthly_contribution_batches"
    __table_args__ = (
        db.UniqueConstraint("year", "month", name="uq_monthly_contribution_period"),
    )

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    account = db.Column(db.String(20), nullable=False, default="bank")
    status = db.Column(db.String(20), nullable=False, default="draft")
    # draft = Prüfung offen, finalized = verbucht

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    finalized_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    finalized_by_user = db.relationship("User", foreign_keys=[finalized_by_user_id])
    finalized_at = db.Column(db.DateTime, nullable=True)

    def period_label(self):
        return f"{self.month:02d}.{self.year}"

    def status_label(self):
        return "verbucht" if self.status == "finalized" else "offen"


class MonthlyContributionPayment(db.Model):
    __tablename__ = "monthly_contribution_payments"
    __table_args__ = (
        db.UniqueConstraint("batch_id", "member_id", name="uq_monthly_contribution_member"),
    )

    id = db.Column(db.Integer, primary_key=True)

    batch_id = db.Column(db.Integer, db.ForeignKey("monthly_contribution_batches.id"), nullable=False)
    batch = db.relationship("MonthlyContributionBatch", backref="payments")

    member_id = db.Column(db.Integer, db.ForeignKey("members.id"), nullable=False)
    member = db.relationship("Member")

    expected_cents = db.Column(db.Integer, nullable=False, default=0)
    paid_cents = db.Column(db.Integer, nullable=False, default=0)
    paid_date = db.Column(db.Date, nullable=True)
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def status_key(self):
        if self.paid_cents <= 0:
            return "open"
        if self.paid_cents < self.expected_cents:
            return "partial"
        if self.paid_cents > self.expected_cents:
            return "overpaid"
        return "paid"

    def status_label(self):
        labels = {
            "open": "offen",
            "partial": "teilweise bezahlt",
            "paid": "bezahlt",
            "overpaid": "überzahlt",
        }
        return labels.get(self.status_key(), "offen")

    def expected_euro(self):
        return f"{self.expected_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def paid_euro(self):
        return f"{self.paid_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def difference_euro(self):
        diff = self.paid_cents - self.expected_cents
        return f"{diff / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class CsvImportRule(db.Model):
    """Gelernte Zuordnung für den CSV-Kontoauszug-Import (Lerneffekt).

    Beim Bestätigen einer CSV-Zeile im Review-Assistenten (Route
    monthly_bank_closing_csv_import) wird die dabei verwendete, ggf. vom
    Kassierer korrigierte Kategorie/Person unter einem aus Buchungstext und
    Verwendungszweck normalisierten Lernschlüssel gespeichert (siehe
    services/csv_import_matching.py: normalize_learning_key). Künftige
    CSV-Zeilen mit demselben Lernschlüssel - z. B. wiederkehrende
    Lastschriften/Daueraufträge mit leicht unterschiedlicher Belegnummer -
    werden dadurch automatisch mit hoher Konfidenz ("gelernt") vorgeschlagen.
    """
    __tablename__ = "csv_import_rules"

    id = db.Column(db.Integer, primary_key=True)

    learning_key = db.Column(db.String(255), unique=True, nullable=False)

    category = db.Column(db.String(80), nullable=False)
    person = db.Column(db.String(160), nullable=True)

    hit_count = db.Column(db.Integer, nullable=False, default=1)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    category = db.Column(db.String(80), nullable=False, default="Sonstiges")
    document_date = db.Column(db.Date, nullable=True)
    description = db.Column(db.Text, nullable=True)

    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False, unique=True)
    mime_type = db.Column(db.String(120), nullable=True)
    file_size = db.Column(db.Integer, nullable=False, default=0)

    event_id = db.Column(db.Integer, db.ForeignKey("bowling_events.id"), nullable=True)
    event = db.relationship("BowlingEvent")

    cashbook_entry_id = db.Column(db.Integer, db.ForeignKey("cashbook_entries.id"), nullable=True)
    # foreign_keys explizit: seit CashbookEntry.source_document_id (CSV-Import,
    # siehe oben) gibt es zwei FK-Pfade zwischen documents und
    # cashbook_entries - ohne explizite Angabe kann SQLAlchemy den
    # Join für diese Relationship sonst nicht mehr eindeutig bestimmen.
    cashbook_entry = db.relationship("CashbookEntry", foreign_keys=[cashbook_entry_id])

    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    uploaded_by_user = db.relationship("User", foreign_keys=[uploaded_by_user_id])
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    deleted_by_user = db.relationship("User", foreign_keys=[deleted_by_user_id])

    def size_label(self):
        size = self.file_size or 0
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB".replace(".", ",")
        if size >= 1024:
            return f"{size / 1024:.1f} KB".replace(".", ",")
        return f"{size} B"

    def category_icon(self):
        icons = {
            "Bahnrechnungen": "🧾",
            "Kassenprüfung": "🔍",
            "Jahresabschluss": "📊",
            "Kegeltour": "🚌",
            "Historische Importdaten": "🗂️",
            "Vereinsunterlagen": "📄",
            "Sonstiges": "📎",
            # ältere Kategorien bleiben aus Kompatibilitätsgründen lesbar
            "Kegelbahn-Rechnung": "🧾",
            "Kontoauszug": "🏦",
            "Vereinsdokument": "📄",
            "Vertrag": "📝",
        }
        return icons.get(self.category, "📎")

    def is_protected_category(self):
        return self.category in {"Kassenprüfung", "Jahresabschluss", "Historische Importdaten"}

    def is_archived(self):
        return self.deleted_at is not None


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User")

    token = db.Column(db.String(128), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)

    def is_active(self):
        return self.used_at is None and self.expires_at >= datetime.utcnow()
