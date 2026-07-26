# 🎳 Kegelkasse

Eine selbst gehostete Vereinsverwaltung für Kegel-/Bowlingvereine: Mitgliederverwaltung, Kassenbuch, Bankkonto & Barkasse, Monatsbeiträge, Strafkonto, Kegelabende, Revision, Statistiken und Datensicherung — alles in einer App.

Entstanden aus dem echten Bedarf eines Kegelvereins, läuft produktiv als Docker-Container.

## Funktionsumfang

- **Mitgliederverwaltung** mit Rollen (Admin, Kassierer, Mitglied, Kassenprüfer)
- **Kegelabende**: Teilnahme, Bahnkosten, Strafen, Abschluss-Workflow
- **Kassenbuch, Bankkonto & Barkasse**, Monatsabschluss, Jahresabschluss
- **Monatsbeiträge** mit automatischer Zinsberechnung
- **Strafkonten** je Mitglied mit frei konfigurierbaren Strafarten
- **Revisionsprotokoll** (Audit-Log) für alle relevanten Änderungen
- **Kassenprüfung** mit eigenständiger, lesender Prüferrolle
- **Statistiken & Exporte** (PDF/CSV)
- **Automatische Datensicherung** inkl. optionaler Cloud-Ziele (WebDAV, Dropbox, Google Drive, OneDrive) und Verschlüsselung
- **Vereins-Export/-Import** für Umzug, Archivierung oder Vereinswechsel

Details zu allen Änderungen: siehe [CHANGELOG.md](CHANGELOG.md).

## Technik

- Python / Flask
- SQLite (Standard), MariaDB optional vorbereitet
- Responsive Weboberfläche, mobile first
- Betrieb per Docker / Docker Compose

## Installation

Voraussetzung: Docker mit Compose-Plugin.

```bash
git clone https://github.com/aluecke75/kegelkasse.git
cd kegelkasse
cp .env.example .env
# .env anpassen: SECRET_KEY, ADMIN_USERNAME/-PASSWORD, GITHUB_TOKEN (siehe unten)
docker compose up -d --build
```

Die App ist danach unter `http://localhost:${APP_PORT}` erreichbar (Port siehe `.env`). Beim ersten Aufruf führt ein Einrichtungsassistent durch die Ersteinrichtung.

### Hinweis zum Bauen des Images

Kegelkasse nutzt für einige gemeinsame Funktionen (Datensicherung, Rechteverwaltung, Theming) das interne Paket **DeveloperKit**. Dieses Paket liegt aktuell noch in einem privaten Repository — zum eigenständigen Bauen des Docker-Images ist daher ein GitHub-Token mit Lesezugriff auf `aluecke75/DeveloperKit` nötig (`GITHUB_TOKEN` in der `.env`). Ohne Zugriff auf dieses Token lässt sich das Image derzeit nicht selbst bauen; eine vollständig eigenständige Nutzung ohne diese Abhängigkeit ist für eine spätere Version geplant.

## Konfiguration

Alle Einstellungen erfolgen über Umgebungsvariablen, siehe [.env.example](.env.example) für die vollständige Liste und Erklärungen (Ports, Datenpfade, zweiter Demo-Container, u. a.).

## Updates

Neue Versionen werden als [GitHub Releases](https://github.com/aluecke75/kegelkasse/releases) veröffentlicht. Ein Admin-Konto in der App zeigt automatisch einen Hinweis in der Kopfzeile an, sobald eine neuere Version verfügbar ist.

Aktualisieren:

```bash
git pull
docker compose up -d --build
```

## Lizenz

Dieses Projekt steht unter der [GNU General Public License v3.0](LICENSE).

## Unterstützen

Kegelkasse wird ehrenamtlich entwickelt und gepflegt. Über eine kleine Spende (☕-Button in der App, oder direkt über [paypal.me/AchimLuecke](https://paypal.me/AchimLuecke)) freue ich mich sehr.
