# 🎳 Kegelkasse

Eine selbst gehostete Vereinsverwaltung für Kegelvereine: Mitgliederverwaltung, Kassenbuch, Bankkonto & Barkasse, Monatsbeiträge, Strafkonto, Kegelabende, Revision, Statistiken und Datensicherung — alles in einer App.

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
- Betrieb per Docker / Docker Compose, läuft z. B. auf einem Synology NAS oder jedem anderen Docker-Host hinter einem beliebigen Reverse Proxy

## Status

Kegelkasse läuft produktiv im Verein, der es hervorgebracht hat. Die Installation läuft über ein vorgefertigtes Docker-Image, ganz ohne eigenen Zugriff auf das private DeveloperKit-Repo. Für die allgemeine Nutzung durch andere Vereine sind noch einige Punkte offen (u. a. mehrmandantenfähige Ersteinrichtung) — siehe [PUBLIC_RELEASE_TODO.md](PUBLIC_RELEASE_TODO.md) für die aktuelle Restliste vor Version 1.0.

## Installation

Voraussetzung: Docker mit Compose-Plugin.

```bash
git clone https://github.com/aluecke75/kegelkasse.git
cd kegelkasse
cp .env.example .env
# .env anpassen: SECRET_KEY, ADMIN_USERNAME/-PASSWORD
docker compose pull
docker compose up -d
```

Die App ist danach unter `http://localhost:${APP_PORT}` erreichbar (Port siehe `.env`). Beim ersten Aufruf führt ein Einrichtungsassistent durch die Ersteinrichtung.

### Alternative: Image selbst bauen

Kegelkasse nutzt für einige gemeinsame Funktionen (Datensicherung, Rechteverwaltung, Theming) das interne Paket **DeveloperKit**, das in einem privaten Repository liegt. Für die normale Installation über das vorgefertigte Image oben spielt das keine Rolle. Wer stattdessen selbst aus dem Quellcode bauen möchte, braucht dafür zusätzlich ein GitHub-Token mit Lesezugriff auf `aluecke75/DeveloperKit` (`GITHUB_TOKEN` in der `.env`):

```bash
docker compose up -d --build
```

## Konfiguration

Alle Einstellungen erfolgen über Umgebungsvariablen, siehe [.env.example](.env.example) für die vollständige Liste und Erklärungen (Ports, Datenpfade, zweiter Demo-Container, u. a.).

## Updates

Neue Versionen werden als [GitHub Releases](https://github.com/aluecke75/kegelkasse/releases) veröffentlicht und automatisch als Docker-Image nach `ghcr.io/aluecke75/kegelkasse` gebaut. Ein Admin-Konto in der App zeigt automatisch einen Hinweis in der Kopfzeile an, sobald eine neuere Version verfügbar ist.

Aktualisieren:

```bash
docker compose pull
docker compose up -d
```

Mit einem Werkzeug wie [Watchtower](https://containrrr.dev/watchtower/) läuft das auch automatisch.

## Datenschutz

Kegelkasse verarbeitet personenbezogene Daten von Vereinsmitgliedern (u. a. Namen, ggf. Bankverbindungen für Beiträge). Als Betreiber einer selbst gehosteten Instanz bist du selbst für den datenschutzkonformen Betrieb (DSGVO) verantwortlich — insbesondere Zugriffsschutz, Verschlüsselung ausgelagerter Datensicherungen (siehe Administration → Datensicherung) und angemessene Aufbewahrungsfristen.

## Fragen & Probleme

Fehler und fehlende Funktionen dürfen gerne gemeldet werden — genauso wie Fragen oder sonstige Vorschläge — bitte über ein [GitHub Issue](https://github.com/aluecke75/kegelkasse/issues). Kegelkasse wird ehrenamtlich in der Freizeit gepflegt, es gibt keine Support-Garantie.

## Lizenz

Dieses Projekt steht unter der [GNU General Public License v3.0](LICENSE).

## Unterstützen

Kegelkasse wird ehrenamtlich entwickelt und gepflegt. Über eine kleine Spende (☕-Button in der App, oder direkt über [paypal.me/AchimLuecke](https://paypal.me/AchimLuecke)) freue ich mich sehr.
