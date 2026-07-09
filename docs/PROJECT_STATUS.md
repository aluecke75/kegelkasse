# Projektstatus

Stand: 2026-07-09

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

**Vereinsversion: ca. 97–98 %.** Kernfunktionen praktisch vollständig. Fokus liegt jetzt auf Feinschliff, Tests, Stabilität und Komfortfunktionen.

Details zu den priorisierten Restarbeiten: siehe [ROADMAP.md](ROADMAP.md).
