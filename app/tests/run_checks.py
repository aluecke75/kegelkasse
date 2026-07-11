"""Kegelkasse - Testskript für Kern-Abläufe.

Prüft nicht nur (wie ein reiner GET-Test), ob sich Seiten öffnen lassen,
sondern auch, ob die wichtigsten SCHREIBENDEN Abläufe funktionieren
(Formular absenden, Datenbank ändern). Genau diese Lücke hat den
audit_log-Importfehler in v0.99.9 monatelang unbemerkt gelassen - ein reiner
GET-Test hätte ihn nie gefunden.

Ausführen (im laufenden Container):
    docker exec kegelkasse python3 tests/run_checks.py

Läuft als eigener Python-Prozess gegen die Datenbank (Flask-Test-Client,
keine echten HTTP-Requests gegen den laufenden Server nötig). Jeder Test
räumt seine eigenen Testdaten wieder auf. Bricht bei einem Fehlschlag NICHT
sofort ab, sondern sammelt alle Ergebnisse und zeigt am Ende eine
Zusammenfassung. Exit-Code 0 nur wenn alles bestanden hat - geeignet, um
nach jeder größeren Änderung (vor allem nach einem Rebuild) kurz
gegenzuprüfen, dass nichts Grundlegendes kaputtgegangen ist.
"""
import os
import sys

# Läuft als "python3 tests/run_checks.py" - Python legt dabei nur das Skript-
# Verzeichnis (tests/) auf sys.path, nicht das übergeordnete app/-Verzeichnis
# mit app.py/models.py. Ohne diese Zeile: "ModuleNotFoundError: No module named 'app'".
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from models import db, User, BowlingEvent, EventParticipant

results = []


def check(name, passed, detail=""):
    results.append((name, passed, detail))
    status = "OK    " if passed else "FEHLER"
    print(f"[{status}] {name}" + (f" - {detail}" if detail and not passed else ""))


GET_ROUTES = [
    "/", "/test-database", "/account", "/admin", "/branding/logo",
    "/documents", "/documents/download/1", "/documents/preview/1",
    "/backups", "/import-export", "/members", "/members/new", "/members/1/edit",
    "/settings/rates", "/settings/event-rhythm", "/settings/rates/new",
    "/settings/penalty-types", "/settings/penalty-types/new", "/settings/penalty-types/1/edit",
    "/cashbook/opening-balances", "/search", "/search?q=a", "/annual-closings",
    "/cash-audits", "/interest", "/cashbook", "/settings/finance",
    "/finance/monthly-bank-closing", "/finance/monthly-contributions",
    "/audit-log", "/reports", "/exports", "/my-event", "/my-event/data",
    "/my-stats", "/events", "/events/new", "/events/1", "/penalty-balances",
]


def check_get_routes(client):
    for route in GET_ROUTES:
        r = client.get(route)
        check(f"GET {route}", r.status_code == 200, f"Status {r.status_code}")


def check_account_edit(client):
    """Eigenes Konto bearbeiten - hat in v0.99.9 mit NameError abgestürzt (audit_log fehlte)."""
    with app.app_context():
        user = db.session.get(User, 1)
        original_email = user.email

    r = client.post("/account", data={
        "username": "admin",
        "email": "kegelkasse-selftest@example.invalid",
        "current_password": "",
        "new_password": "",
        "new_password_repeat": "",
    }, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /account (E-Mail ändern)", ok, f"Status {r.status_code}")

    # zurücksetzen
    client.post("/account", data={
        "username": "admin",
        "email": original_email or "",
        "current_password": "",
        "new_password": "",
        "new_password_repeat": "",
    })
    with app.app_context():
        user = db.session.get(User, 1)
        check("Konto-E-Mail zurückgesetzt", user.email == original_email)


def check_password_forgot(client):
    """Passwort-vergessen-Formular - hat in v0.99.9 mit NameError abgestürzt (audit_log fehlte)."""
    r = client.post("/password-forgot", data={"username": "admin"}, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /password-forgot", ok, f"Status {r.status_code}")


def check_admin_setting_toggle(client):
    """Eine einfache Admin-Einstellung ändern und wieder zurücksetzen."""
    from services.settings import setting_value

    with app.app_context():
        old_value = setting_value("event_closed_confirmation_enabled", "0")
    new_value = "0" if old_value == "1" else "1"

    r = client.post("/admin", data={
        "form_action": "event_settings",
        "event_closed_confirmation_enabled": "1" if new_value == "1" else "",
    }, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /admin (Einstellung ändern)", ok, f"Status {r.status_code}")

    client.post("/admin", data={
        "form_action": "event_settings",
        "event_closed_confirmation_enabled": "1" if old_value == "1" else "",
    })
    with app.app_context():
        restored = setting_value("event_closed_confirmation_enabled", "0")
        check("Admin-Einstellung zurückgesetzt", restored == old_value)


def check_backup_lifecycle(client):
    """Lokale Sicherung erstellen, prüfen, löschen - ohne Cloud-Ziel."""
    r = client.post("/backups", data={"form_action": "create_backup"}, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /backups (create_backup)", ok, f"Status {r.status_code}")
    if not ok:
        return

    from routes.backups import backup_file_list
    with app.app_context():
        files = backup_file_list()
        manual = next((f for f in files if f["kind"] == "Manuell"), None)

    if not manual:
        check("Testsicherung gefunden", False, "keine manuelle Sicherung in der Liste")
        return

    r = client.post("/backups", data={"form_action": "check_backup", "filename": manual["name"]}, follow_redirects=True)
    check("POST /backups (check_backup)", r.status_code == 200 and b"Internal Server" not in r.data)

    r = client.post("/backups", data={"form_action": "delete_backup", "filename": manual["name"]}, follow_redirects=True)
    check("POST /backups (delete_backup, aufräumen)", r.status_code == 200 and b"Internal Server" not in r.data)


def check_event_lifecycle(client):
    """Kegelabend anlegen und als ausgefallen markieren (übt event_created/event_cancelled +
    audit_log/audit_diff_lines). Deckt nicht den vollen Abrechnungs-Ablauf ab (Barzahlungen,
    Bahnkosten) - das erfordert dynamisch benannte Teilnehmerfelder und wurde beim Bau des
    Features bereits ausführlich manuell getestet."""
    r = client.post("/events/new", data={
        "event_date": "2099-01-01",
        "note": "",
        "is_cancelled": "0",
        "force_new": "1",
    }, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /events/new (Kegelabend anlegen)", ok, f"Status {r.status_code}")
    if not ok:
        return

    with app.app_context():
        event = BowlingEvent.query.order_by(BowlingEvent.id.desc()).first()
        event_id = event.id if event and str(event.event_date) == "2099-01-01" else None

    if not event_id:
        check("Test-Kegelabend gefunden", False, "konnte angelegten Testabend nicht wiederfinden")
        return

    r = client.post(f"/events/{event_id}", data={
        "action": "cancel_event",
        "cancel_reason": "Automatischer Selbsttest - bitte ignorieren",
        "cancel_note": "",
    }, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /events/<id> (cancel_event)", ok, f"Status {r.status_code}")

    with app.app_context():
        EventParticipant.query.filter_by(event_id=event_id).delete()
        db.session.delete(db.session.get(BowlingEvent, event_id))
        db.session.commit()
        check("Test-Kegelabend aufgeräumt", db.session.get(BowlingEvent, event_id) is None)


def run():
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        check_get_routes(client)
        check_account_edit(client)
        check_password_forgot(client)
        check_admin_setting_toggle(client)
        check_backup_lifecycle(client)
        check_event_lifecycle(client)

    failed = [r for r in results if not r[1]]
    print()
    print(f"{len(results) - len(failed)}/{len(results)} Prüfungen bestanden.")
    if failed:
        print("Fehlgeschlagen:")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    run()
