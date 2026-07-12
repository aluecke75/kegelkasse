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

from datetime import datetime, timedelta

from app import app
from models import db, User, BowlingEvent, EventParticipant, AuditLog

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


def check_account_edit(client, admin_id, admin_username):
    """Eigenes Konto bearbeiten - hat in v0.99.9 mit NameError abgestürzt (audit_log fehlte)."""
    with app.app_context():
        user = db.session.get(User, admin_id)
        original_email = user.email

    r = client.post("/account", data={
        "username": admin_username,
        "email": "kegelkasse-selftest@example.invalid",
        "current_password": "",
        "new_password": "",
        "new_password_repeat": "",
    }, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /account (E-Mail ändern)", ok, f"Status {r.status_code}")

    # zurücksetzen
    client.post("/account", data={
        "username": admin_username,
        "email": original_email or "",
        "current_password": "",
        "new_password": "",
        "new_password_repeat": "",
    })
    with app.app_context():
        user = db.session.get(User, admin_id)
        check("Konto-E-Mail zurückgesetzt", user.email == original_email)


def check_password_forgot(client, admin_id, admin_username):
    """Passwort-vergessen-Formular - hat in v0.99.9 mit NameError abgestürzt (audit_log fehlte).

    Formularfeld heißt "lookup", nicht "username" - das war hier lange falsch
    und hat nur den harmlosen "kein Konto gefunden"-Pfad getestet, nie den
    eigentlichen (audit_log-nutzenden) Erfolgspfad. Erzeugt jetzt echt einen
    Reset-Token für den Admin, deshalb hinterher wieder aufräumen.
    """
    from models import PasswordResetToken, AuditLog

    r = client.post("/password-forgot", data={"lookup": admin_username}, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /password-forgot", ok, f"Status {r.status_code}")

    # Nur den jeweils neuesten (gerade durch diesen Test erzeugten) Token/
    # Revisionsprotokoll-Eintrag löschen - nicht pauschal alle für diesen
    # Admin, das könnte echte frühere Reset-Vorgänge treffen.
    with app.app_context():
        newest_token = PasswordResetToken.query.filter_by(user_id=admin_id).order_by(PasswordResetToken.id.desc()).first()
        if newest_token:
            db.session.delete(newest_token)
        newest_entry = AuditLog.query.filter_by(
            action="password_reset_requested", object_type="User", object_id=admin_id,
        ).order_by(AuditLog.id.desc()).first()
        if newest_entry:
            db.session.delete(newest_entry)
        db.session.commit()


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


def check_audit_log_cleanup(client):
    """Revisionsprotokoll bereinigen: Aufbewahrungsfrist speichern, alte
    operative Einträge werden gelöscht, geschützte Kategorien bleiben
    unberührt, manuelles Einzel-Löschen funktioniert."""
    from services.audit import cleanup_old_audit_log_entries
    from services.settings import setting_value, set_setting_value

    old_retention = setting_value("audit_log_retention_days", "0")

    with app.app_context():
        old_ts = datetime.utcnow() - timedelta(days=1000)
        operational = AuditLog(
            created_at=old_ts, category="Datensicherung", action="selftest",
            title="Selbsttest - alt, operativ (sollte gelöscht werden)",
        )
        protected = AuditLog(
            created_at=old_ts, category="finance", action="selftest",
            title="Selbsttest - alt, geschützt (sollte bleiben)",
        )
        db.session.add_all([operational, protected])
        db.session.commit()
        operational_id, protected_id = operational.id, protected.id

        deleted = cleanup_old_audit_log_entries(365)
        db.session.commit()
        check("Bereinigung löscht mindestens den Test-Eintrag", deleted >= 1, f"deleted={deleted}")
        check("Operativer Test-Eintrag wurde gelöscht", db.session.get(AuditLog, operational_id) is None)
        check("Geschützter Test-Eintrag (finance) blieb erhalten", db.session.get(AuditLog, protected_id) is not None)

    r = client.post("/audit-log", data={
        "form_action": "audit_log_retention_settings",
        "audit_log_retention_days": "730",
    }, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /audit-log (Aufbewahrungsfrist speichern)", ok, f"Status {r.status_code}")

    with app.app_context():
        manual_entry = AuditLog(category="finance", action="selftest", title="Selbsttest - manuell löschen")
        db.session.add(manual_entry)
        db.session.commit()
        manual_id = manual_entry.id

    r = client.post("/audit-log", data={
        "form_action": "audit_log_delete_entries",
        "selected_entries": str(manual_id),
    }, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /audit-log (manuell löschen)", ok, f"Status {r.status_code}")
    with app.app_context():
        check("Manuell ausgewählter Eintrag wurde gelöscht", db.session.get(AuditLog, manual_id) is None)
        # etwaigen "Bereinigung"-Revisionsprotokoll-Eintrag mit aufräumen
        AuditLog.query.filter(AuditLog.title.ilike("Selbsttest%")).delete(synchronize_session=False)
        db.session.commit()

    with app.app_context():
        set_setting_value("audit_log_retention_days", old_retention or "0")
        db.session.commit()
        restored = setting_value("audit_log_retention_days", "0")
        check("Aufbewahrungsfrist zurückgesetzt", restored == (old_retention or "0"))


def run():
    from config import get_active_database_profile

    profile = get_active_database_profile()
    if profile == "production" and os.getenv("KEGELKASSE_ALLOW_TEST_ON_PRODUCTION") != "1":
        print(
            "ABGEBROCHEN: aktive Datenbank ist 'production' (die echte Vereinsdatenbank), "
            "nicht die Demo-Datenbank. Dieses Skript legt/ändert/löscht testweise echte "
            "Datensätze (auch wenn es sie danach wieder aufräumt) - das soll nur gegen "
            "die Demo-Datenbank laufen. Bitte im Adminbereich auf die Demo-Datenbank "
            "umschalten (verstecktes 🎳-Symbol oben links), oder falls das wirklich "
            "gewollt ist: KEGELKASSE_ALLOW_TEST_ON_PRODUCTION=1 setzen."
        )
        sys.exit(2)

    with app.app_context():
        admin = User.query.filter_by(role="admin", active=True).first()
    if not admin:
        print("ABGEBROCHEN: kein aktiver Admin-Benutzer gefunden, gegen den getestet werden könnte.")
        sys.exit(2)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True

        check_get_routes(client)
        check_account_edit(client, admin.id, admin.username)
        check_password_forgot(client, admin.id, admin.username)
        check_admin_setting_toggle(client)
        check_backup_lifecycle(client)
        check_event_lifecycle(client)
        check_audit_log_cleanup(client)

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
