# Projektstatus

Stand: 2026-07-12

## Überblick

Kegelkasse ist von einer einfachen Vereinskasse zu einer vollständigen Vereinsverwaltung für Kegelvereine gewachsen:

- Mitgliederverwaltung
- Strafkonto
- Kegelabende
- Monatsbeiträge
- Kassenbuch
- Bankkonto & Barkasse
- Revision
- Statistiken
- Backups
- Dokumente
- Rechteverwaltung

## Technik

- Python / Flask
- SQLite (Vereinsversion)
- Docker, betrieben auf Synology NAS
- Docker-Pfad: `/volume1/docker/kegelkasse`, Webport 8091
- Reverse Proxy: https://kegeln.aluecke.synology.me
- Responsive Weboberfläche, mobile first, spätere PWA geplant

## Design-Standard

- Modernes Kartenlayout, große Buttons, Dashboard, Icons, grüne Hauptfarbe
- Logo-Favorit: drei Kegel, dunkelgrüne Kegelkugel ohne Fingerlöcher, goldene Münzen, grün/gold
- Slogan: "Gemeinsam kegeln. Gemeinsam Kasse."

## Benutzerrollen

- **Admin**: Vollzugriff
- **Kassierer**: Mitglieder, Kegelabende, Korrekturen, Buchungen
- **Mitglied**: eigene Daten, Strafen erfassen, Mein Kegelabend
- **Mitglied ohne Login**: nur Anzeige
- **Kassenprüfer**: nur lesen, Revision bestätigen
- **Systemadmin** (spätere öffentliche Version): nicht selbst Vereinsmitglied

## Versionsstand

**Vereinsversion: inhaltlich fertig (aktuell v0.99.11).** Von den 11 priorisierten Restarbeiten aus der Roadmap sind 10 vollständig abgeschlossen, nur ein kleinerer Punkt beim Revisionsprotokoll ist noch offen (siehe [ROADMAP.md](ROADMAP.md)).

Seit dem letzten Stand (2026-07-09) kam vor allem hinzu:

- **Code-Struktur grundlegend überarbeitet:** `app.py` war auf ~9100 Zeilen angewachsen und wurde vollständig in `services/` (reine Hilfsfunktionen) und `routes/` (Routen je Fachbereich) aufgeteilt — bewusst ohne Flask-Blueprints, damit alle Endpoint-Namen/`url_for()`-Aufrufe unverändert bleiben.
- **Verschlüsselung für Cloud-Backup-Kopien:** neu, mit zwei wählbaren Methoden (Schlüsselpaar ohne Passwort, oder klassisches Passwort) — nur die Kopie am externen Backup-Ziel wird verschlüsselt, lokale Sicherungen bleiben unverändert sofort wiederherstellbar. Erste echte Abhängigkeit über den Flask-Stack hinaus (`cryptography`).
- **Kegelabend-Protokoll als PDF:** druckbares Querformat-Dokument für einen einzelnen abgeschlossenen Kegelabend, als Papier-Rückfallebene falls die App mal nicht erreichbar ist.
- Diverse Bugfixes (u. a. zwei kritische Abstürze, die erst durch systematische Code-Durchsicht bzw. echte Schreib-Tests gefunden wurden) und kleinere UX-Verbesserungen (Vereinslogo admin-konfigurierbar anzeigbar, Backup-Sammellöschen, Dropbox/Google Drive/OneDrive-Verbindung repariert).
- **Dauerhaftes Testskript** (`app/tests/run_checks.py`) ins Repo aufgenommen, das neben allen wichtigen Seiten auch die wichtigsten schreibenden Abläufe mit echter Zustandsänderung prüft.

Details zu allen Versionen: siehe [CHANGELOG.md](../CHANGELOG.md).
