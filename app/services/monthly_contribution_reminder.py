"""Erinnert den/die Kassierer per E-Mail, sobald der Dashboard-Hinweis
"Monatsbeiträge prüfen" fällig wird. Wird lazy bei jedem Dashboard-Aufruf
(GET /) für Admins/Kassierer geprüft - analog zu services/update_check.py.
Schlägt niemals sichtbar fehl (kein Mailserver konfiguriert, SMTP down,
kein Kassierer mit E-Mail hinterlegt -> einfach kein Versand), damit ein
Mailproblem nie das Dashboard kaputt machen kann."""
from models import db, User
from services.settings import setting_value, set_setting_value
from services.mail import send_system_mail
from services.dates import month_label

REMINDER_SETTING_KEY = "monthly_contribution_reminder_sent_period"


def maybe_send_cashier_reminder(expected_year, expected_month):
    """Verschickt einmal pro fälligem Beitragszeitraum eine Erinnerungsmail an
    alle aktiven Kassierer mit hinterlegter E-Mail-Adresse. Wird aus
    dashboard() aufgerufen, sobald der zugehörige Hinweis fällig ist
    (monthly_contribution_task_due). Wirft nie eine Exception nach außen."""
    try:
        period = f"{expected_year:04d}-{expected_month:02d}"
        if setting_value(REMINDER_SETTING_KEY) == period:
            return  # für diesen Zeitraum schon erledigt

        cashiers = (
            User.query
            .filter(User.role == "cashier")
            .filter(User.active == True)  # noqa: E712  SQLAlchemy-Vergleich, kein Python-Bool-Check
            .filter(User.email.isnot(None))
            .filter(User.email != "")
            .all()
        )

        if not cashiers:
            return  # niemand zu benachrichtigen - beim nächsten Aufruf erneut versuchen

        subject = f"Kegelkasse: Monatsbeiträge {month_label(expected_year, expected_month)} noch offen"
        body = (
            f"Hallo,\n\n"
            f"die Monatsbeiträge für {month_label(expected_year, expected_month)} sind in der Kegelkasse "
            f"noch nicht erfasst bzw. abgeschlossen.\n\n"
            f"Bitte im Menüpunkt \"Monatsbeiträge\" prüfen und bei Bedarf abschließen.\n\n"
            f"Viele Grüße\nKegelkasse"
        )

        any_sent = False
        for cashier in cashiers:
            try:
                success, _message = send_system_mail(cashier.email, subject, body)
                if success:
                    any_sent = True
            except Exception:
                pass

        if any_sent:
            set_setting_value(REMINDER_SETTING_KEY, period)
            db.session.commit()
    except Exception:
        pass
