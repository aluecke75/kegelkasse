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
    "/", "/account", "/admin", "/branding/logo",
    "/documents", "/documents/download/1", "/documents/preview/1",
    "/verwaltung/backups/", "/import-export", "/members", "/members/new", "/members/1/edit",
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
    """Lokale Sicherung erstellen, prüfen, löschen - ohne Cloud-Ziel.

    Die allgemeine Datensicherung lebt seit der Umstellung auf das
    gemeinsame DeveloperKit-Backup-Modul unter /verwaltung/backups
    (siehe app.py). Der alte Pfad /backups (Kegelkasse-eigener Code) ist
    entfallen; /backups/import-export (Vereins-Export/-Import) blieb
    unverändert Kegelkasse-eigen."""
    r = client.post("/verwaltung/backups/", data={"form_action": "create_backup"}, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /verwaltung/backups/ (create_backup)", ok, f"Status {r.status_code}")
    if not ok:
        return

    from developerkit.backup.service import backup_file_list

    with app.app_context():
        files = backup_file_list()
        manual = next((f for f in files if f["kind"] == "Manuell"), None)

    if not manual:
        check("Testsicherung gefunden", False, "keine manuelle Sicherung in der Liste")
        return

    r = client.post(
        "/verwaltung/backups/", data={"form_action": "check_backup", "filename": manual["name"]}, follow_redirects=True
    )
    check("POST /verwaltung/backups/ (check_backup)", r.status_code == 200 and b"Internal Server" not in r.data)

    r = client.post(
        "/verwaltung/backups/", data={"form_action": "delete_backup", "filename": manual["name"]}, follow_redirects=True
    )
    check("POST /verwaltung/backups/ (delete_backup, aufräumen)", r.status_code == 200 and b"Internal Server" not in r.data)


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


def check_csv_import_review(client):
    """CSV-Kontoauszug-Datenübernahme (Review-Assistent monthly_bank_closing_csv_import):
    Konfidenz-Vorschläge (gelernt/vermutet/kein Treffer), Lerneffekt beim
    Bestätigen, weicher Monatsbeitrags-Dubletten-Hinweis, Sperre für bereits
    abgeschlossene Jahre und Idempotenz (source_document_id/source_row_index -
    keine Doppelbuchung bei erneutem Aufruf)."""
    from pathlib import Path
    from models import (
        Document, CashbookEntry, AccountTransaction, CsvImportRule,
        Member, MonthlyContributionBatch, MonthlyContributionPayment, AnnualClosing,
    )

    marker = "SELBSTTESTCSVIMPORT"
    test_year, test_month = 2099, 1
    month_value = f"{test_year:04d}-{test_month:02d}"

    csv_text = "\n".join([
        "Datum;Buchungstext;Verwendungszweck;Betrag",
        f"01.01.{test_year};Überweisung;{marker} ohne Treffer;12,34",
        f"02.01.{test_year};Lastschrift;{marker} Mitgliedsbeitrag Max Mustermann Re-Nr 000123;20,00",
        f"03.01.{test_year};Dauerauftrag;{marker} Miete Kegelbahn Halle Re-Nr 000555;-45,00",
        f"04.01.{test_year};Buchung;{marker} ungueltige Zeile;abc",
        "05.01.2020;Überweisung;" + marker + " gesperrtes Jahr;10,00",
    ]) + "\n"

    with app.app_context():
        from app import active_database_info

        member = Member.query.filter_by(first_name="Max", last_name="Mustermann").first()
        if not member:
            check("Testmitglied für CSV-Import-Check gefunden", False, "Mitglied 'Max Mustermann' nicht in der Demo-Datenbank gefunden")
            return
        member_display_name = member.display_name()

        documents_dir = Path(active_database_info()["documents_path"])
        documents_dir.mkdir(parents=True, exist_ok=True)
        stored_filename = "selftest_csv_import.csv"
        target = documents_dir / stored_filename
        target.write_text(csv_text, encoding="utf-8")

        document = Document(
            title="Selbsttest Kontoauszug CSV",
            category="Kontoauszug CSV",
            document_date=datetime(test_year, test_month, 1).date(),
            description="Automatischer Selbsttest - bitte ignorieren",
            original_filename="selftest.csv",
            stored_filename=stored_filename,
            mime_type="text/csv",
            file_size=target.stat().st_size,
        )
        db.session.add(document)
        db.session.flush()
        document_id = document.id

        batch = MonthlyContributionBatch(year=test_year, month=test_month, account="bank", status="draft")
        db.session.add(batch)
        db.session.flush()
        db.session.add(MonthlyContributionPayment(
            batch_id=batch.id, member_id=member.id,
            expected_cents=2000, paid_cents=2000,
            paid_date=datetime(test_year, test_month, 7).date(),
        ))

        db.session.add(AnnualClosing(year=2020, closing_date=datetime(2021, 1, 15).date()))
        db.session.commit()

        from services.csv_import_matching import suggest_for_row, monthly_contribution_duplicate_hint

        suggestion = suggest_for_row("Lastschrift", f"{marker} Mitgliedsbeitrag Max Mustermann Re-Nr 000123", 2000)
        check(
            "Vermutet-Konfidenz erkennt Mitgliedsnamen im Verwendungszweck",
            suggestion["confidence"] == "guessed" and suggestion["category"] == "Mitgliedsbeitrag" and suggestion["person"] == member_display_name,
            str(suggestion),
        )

        hint = monthly_contribution_duplicate_hint(
            datetime(test_year, test_month, 2).date(), 2000, "Lastschrift", f"{marker} Mitgliedsbeitrag Max Mustermann Re-Nr 000123"
        )
        check("Dubletten-Hinweis erkennt bereits gebuchten Monatsbeitrag (gleicher Betrag/Zeitraum/Name)", hint is not None, str(hint))

    r = client.get(f"/finance/monthly-bank-closing/csv-import?month={month_value}")
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("GET /finance/monthly-bank-closing/csv-import", ok, f"Status {r.status_code}")
    body = r.data.decode("utf-8") if ok else ""
    check("Review-Seite zeigt 'vermutet'-Badge", "vermutet" in body)
    check("Review-Seite zeigt Dubletten-Hinweis-Text", "Möglicher Treffer" in body)

    # Zeile 1 (kein Treffer), 3 (Ausgabe -> soll gelernt werden) und 4/5 (ungültig
    # bzw. gesperrtes Jahr - dürfen trotz "take" NICHT gebucht werden) übernehmen.
    # Zeile 2 (nur "vermutet") wird bewusst NICHT ausgewählt - Design-Vorgabe:
    # kein automatisches Buchen ohne bewusste Bestätigung.
    r = client.post("/finance/monthly-bank-closing/csv-import", data={
        "month": month_value,
        "take": ["1", "3", "4", "5"],
        "category_1": "Sonstige Einnahme",
        "person_1": "",
        "category_3": "Bahnkosten",
        "person_3": "Kegelbahn Selbsttest",
    }, follow_redirects=True)
    ok = r.status_code == 200 and b"Internal Server" not in r.data
    check("POST /finance/monthly-bank-closing/csv-import (Zeilen übernehmen)", ok, f"Status {r.status_code}")

    with app.app_context():
        entries = CashbookEntry.query.filter_by(source_document_id=document_id).all()
        booked_indexes = {e.source_row_index for e in entries}

        check("Zeile 1 (kein Treffer, bewusst ausgewählt) wurde gebucht", 1 in booked_indexes)
        check("Zeile 2 (nur vermutet, NICHT ausgewählt) wurde NICHT gebucht", 2 not in booked_indexes)
        check("Zeile 3 (Ausgabe) wurde gebucht", 3 in booked_indexes)
        check("Zeile 4 (ungültiger Betrag) wurde trotz Auswahl NICHT gebucht", 4 not in booked_indexes)
        check("Zeile 5 (gesperrtes Jahr 2020) wurde trotz Auswahl NICHT gebucht", 5 not in booked_indexes)

        entry3 = next((e for e in entries if e.source_row_index == 3), None)
        check(
            "Zeile 3 korrekt als Bank-Ausgabe gebucht (Betrag/Konto/Richtung)",
            entry3 is not None and entry3.direction == "expense" and entry3.account == "bank" and entry3.amount_cents == 4500,
        )

        rule = CsvImportRule.query.filter(CsvImportRule.learning_key.like(f"%{marker}%KEGELBAHN%")).first()
        check("Lerneffekt: Regel für Zeile 3 wurde gespeichert (Kategorie+Person)", rule is not None and rule.category == "Bahnkosten" and rule.person == "Kegelbahn Selbsttest")

        relearned = suggest_for_row("Dauerauftrag", f"{marker} Miete Kegelbahn Halle Re-Nr 000999", -4500)
        check(
            "Gelernte Regel wird bei abweichender Belegnummer als 'gelernt' vorgeschlagen",
            relearned["confidence"] == "learned" and relearned["category"] == "Bahnkosten" and relearned["person"] == "Kegelbahn Selbsttest",
            str(relearned),
        )

        transactions = AccountTransaction.query.filter_by(category="csv_import").filter(
            AccountTransaction.booking_date.in_([datetime(test_year, 1, 1).date(), datetime(test_year, 1, 3).date()])
        ).all()
        check("AccountTransaction je gebuchter CSV-Zeile angelegt", len(transactions) == 2, f"gefunden: {len(transactions)}")

    # Erneuter Aufruf der Review-Seite: bereits übernommene Zeile ist markiert.
    r = client.get(f"/finance/monthly-bank-closing/csv-import?month={month_value}")
    check("Bereits übernommene Zeile wird als solche markiert", "bereits übernommen" in r.data.decode("utf-8"))

    # Kassenprüfer (Rolle "auditor") dürfen die Review-Seite laut @role_required
    # aufrufen - das globale Auditor-Lesemodus-Skript in base.html blendet aber
    # jedes <form method="post"> clientseitig komplett aus. Die Review-Tabelle
    # rendert für sie deshalb bewusst in einem <div> statt <form> (siehe
    # Template). Hier direkt am servergenerierten HTML geprüft, ohne echten
    # Browser/JS.
    with app.app_context():
        auditor = User.query.filter_by(role="auditor", active=True).first()
        admin_for_restore = User.query.filter_by(role="admin", active=True).first()
    if not auditor:
        check("Kassenprüfer-Testbenutzer für Sichtbarkeits-Check gefunden", False, "keine aktive Rolle 'auditor' in der Demo-Datenbank gefunden")
    else:
        # Bewusst derselbe Client (nicht ein zweiter app.test_client()): verschachtelte
        # test_client()-Instanzen teilen sich denselben Flask-Kontext-Stack und können
        # sich gegenseitig die Session "stehlen" (führte hier zu einem Testartefakt -
        # der Prüfer erschien als admin authentifiziert, obwohl seine Session korrekt
        # gesetzt war). Stattdessen: Session desselben Clients kurz umschalten, danach
        # zurück auf admin, damit die nachfolgenden Prüfungen unverändert weiterlaufen.
        with client.session_transaction() as sess:
            sess["_user_id"] = str(auditor.id)
            sess["_fresh"] = True
        r = client.get(f"/finance/monthly-bank-closing/csv-import?month={month_value}")
        body = r.data.decode("utf-8")
        check("Kassenprüfer: Review-Seite lädt (Status 200)", r.status_code == 200)
        check(
            "Kassenprüfer: Review-Tabelle steckt in einem <div>, nicht in einem <form> (würde sonst clientseitig komplett ausgeblendet)",
            '<div id="csvImportForm">' in body and '<form method="post" id="csvImportForm">' not in body,
        )
        check("Kassenprüfer: sieht die gebuchte Kategorie 'Bahnkosten' trotzdem im Klartext", "Bahnkosten" in body)
        with client.session_transaction() as sess:
            sess["_user_id"] = str(admin_for_restore.id)
            sess["_fresh"] = True

    # Idempotenz: erneutes Übernehmen derselben Zeile darf nicht doppelt buchen.
    client.post("/finance/monthly-bank-closing/csv-import", data={
        "month": month_value, "take": ["1"], "category_1": "Sonstige Einnahme",
    }, follow_redirects=True)
    with app.app_context():
        count_row1 = CashbookEntry.query.filter_by(source_document_id=document_id, source_row_index=1).count()
        check("Erneutes Übernehmen derselben Zeile bucht nicht doppelt (Idempotenz)", count_row1 == 1, f"gefunden: {count_row1}")

    # Ein doppelt übermittelter "take"-Wert innerhalb *eines* Requests (z. B. durch
    # eine manipulierte Anfrage, da das deaktivierte Checkbox-Attribut clientseitig
    # keinen echten Schutz bietet) darf dieselbe, bisher nicht gebuchte Zeile 2
    # trotzdem nur einmal buchen.
    client.post("/finance/monthly-bank-closing/csv-import", data={
        "month": month_value, "take": ["2", "2"],
        "category_2": "Mitgliedsbeitrag", "person_2": member_display_name,
    }, follow_redirects=True)
    with app.app_context():
        count_row2 = CashbookEntry.query.filter_by(source_document_id=document_id, source_row_index=2).count()
        check("Doppelter 'take'-Wert im selben Request bucht nicht doppelt", count_row2 == 1, f"gefunden: {count_row2}")

    # Eine stornierte CSV-Buchung (is_void) muss wieder zur Übernahme angeboten
    # werden - sonst bliebe eine korrigierte Zeile für immer gesperrt, obwohl sie
    # tatsächlich nicht mehr in der Kasse steht.
    with app.app_context():
        entry_row2 = CashbookEntry.query.filter_by(source_document_id=document_id, source_row_index=2).first()
        entry_row2.is_void = True
        db.session.commit()

        from routes.monthly_contributions import build_csv_import_review_rows
        document_obj = db.session.get(Document, document_id)
        _, rows_after_void = build_csv_import_review_rows(document_obj)
        row2_after_void = next((r for r in rows_after_void if r["index"] == 2), None)
        check(
            "Stornierte Zeile gilt wieder als nicht übernommen und kann erneut ausgewählt werden",
            row2_after_void is not None and row2_after_void["already_imported"] is False,
        )

    client.post("/finance/monthly-bank-closing/csv-import", data={
        "month": month_value, "take": ["2"],
        "category_2": "Mitgliedsbeitrag", "person_2": member_display_name,
    }, follow_redirects=True)
    with app.app_context():
        active_row2_count = CashbookEntry.query.filter_by(
            source_document_id=document_id, source_row_index=2, is_void=False
        ).count()
        check(
            "Nach Stornierung erneut übernommene Zeile legt genau eine neue aktive Buchung an",
            active_row2_count == 1, f"gefunden: {active_row2_count}",
        )

    # Aufräumen.
    with app.app_context():
        CashbookEntry.query.filter_by(source_document_id=document_id).delete(synchronize_session=False)
        AccountTransaction.query.filter_by(category="csv_import").filter(
            AccountTransaction.booking_date.in_([
                datetime(test_year, 1, 1).date(),
                datetime(test_year, 1, 2).date(),
                datetime(test_year, 1, 3).date(),
            ])
        ).delete(synchronize_session=False)
        CsvImportRule.query.filter(CsvImportRule.learning_key.like(f"%{marker}%")).delete(synchronize_session=False)
        MonthlyContributionPayment.query.filter_by(batch_id=batch.id).delete(synchronize_session=False)
        MonthlyContributionBatch.query.filter_by(year=test_year, month=test_month).delete(synchronize_session=False)
        AnnualClosing.query.filter_by(year=2020).delete(synchronize_session=False)
        Document.query.filter_by(id=document_id).delete(synchronize_session=False)
        db.session.commit()
        if target.exists():
            target.unlink()
        check(
            "Selbsttest-Daten (CSV-Import) aufgeräumt",
            CashbookEntry.query.filter_by(source_document_id=document_id).count() == 0
            and Document.query.filter_by(id=document_id).count() == 0,
        )


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
        check_csv_import_review(client)
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
