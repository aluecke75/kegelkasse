# 🎳 Kegelkasse

Eine moderne, selbst gehostete Kegelkasse für Kegelvereine und Kegelclubs.

Entstanden aus der Praxis eines echten Vereins mit dem Ziel, die Verwaltung von Strafgeldern, Guthaben, Kassenbestand und Kegelabenden deutlich zu vereinfachen.

---

# Aktueller Status

🚧 Aktive Entwicklung

Die Software befindet sich im produktiven Testbetrieb innerhalb eines Kegelvereins und wird laufend erweitert.

---

# Ziele des Projekts

Die Kegelkasse soll typische Aufgaben eines Kassenwarts digital unterstützen:

* Mitglieder verwalten
* Kegelabende erfassen
* Strafgelder berechnen
* Guthaben verwalten
* Offene Forderungen verwalten
* Bar- und Bankkasse führen
* Monatsbeiträge erfassen
* Statistiken und Auswertungen erstellen
* Self-Hosting auf Synology NAS, Linux oder Docker-Server

---

# Bereits umgesetzt

## Benutzer & Rollen

* Login-System
* Rollenverwaltung
* Admin
* Kassierer
* Mitglied
* Mitglieder ohne Benutzerkonto

## Mitgliederverwaltung

* Mitglieder anlegen
* Mitglieder bearbeiten
* Spitznamen
* Gastkegler
* Aktiv/Inaktiv

## Finanzverwaltung

* Barkasse
* Bankkonto
* Startbestände
* Umbuchungen
* Kassenbuch
* Monatsbeiträge
* Zinsverwaltung

## Kegelabende

* Kegelabend anlegen
* Kegelabend bearbeiten
* Kegelabend abschließen
* Historische Kegelabende
* Gastkegler
* Ausfalltage

## Strafarten

* Dynamische Strafarten
* Aktiv/Inaktiv
* Reihenfolge frei wählbar
* Beträge frei konfigurierbar
* Unterstützung für:

  * Strafen für den Verursacher
  * Strafen für alle anderen Spieler

## Docker

* Docker Compose
* Synology NAS kompatibel
* Reverse Proxy geeignet
* GitHub Backup
* Gitea Integration

---

# Bereits geplante Funktionen

## Strafkonto

Jedes Mitglied erhält ein eigenes Strafkonto.

Unterstützt:

* Offene Strafbeträge
* Guthaben
* Teilzahlungen
* Überzahlungen
* Nachträgliche Überweisungen

Beispiel:

* Strafen: 7,30 €
* Bezahlt: 10,00 €
* Guthaben: 2,70 €

---

## Zahlungsverwaltung

Nach einem Kegelabend:

1. Strafen berechnen
2. Barzahlungen erfassen
3. Offene Beträge buchen
4. Guthaben buchen
5. Bahnkosten verbuchen, Rechnung per Foto erfassen
6. Kegelabend abschließen

Spätere Überweisungen sollen unabhängig vom Kegelabend verbucht werden können.

---

## Statistiken

* Pumpenkönig
* Kranzkönig
* Alle Neune
* Fehltage
* Strafen pro Jahr
* Strafen pro Spieler
* Jahresauswertungen
* Mitgliederübersichten

---

## Berichte

* PDF-Berichte
* Jahresberichte
* Kassenberichte
* Mitgliederberichte
* Exportfunktionen

---

## Mobile Nutzung

* Optimierung für Mobile Erfassung der Strafen
* Kegelabend direkt am Handy erfassen
* Große Plus-/Minus-Tasten 
* Live-Aktualisierung: Jedes Mitglied kann an seinem eigenen Mobilgerät seine eigenen Strafen und Strafbeträge mitverfolgen
* Geplant:
* Offline Erfassung der Strafen mit Nachträglicher Synchronisierung, sobald das Gerät wieder Online ist.

---

## Sicherheit

* Schreibsperren bei gleichzeitiger Bearbeitung
* Revisionsprotokoll
* Backup-System
* Erweiterte Benutzerrechte

---

# Technische Daten

## Backend

* Python
* Flask
* SQLAlchemy
* SQLite

## Frontend

* HTML
* Jinja2
* Bootstrap

## Deployment

* Docker
* Docker Compose
* Synology NAS

---

# Datensicherheit

Folgende Dateien werden bewusst nicht im Git-Repository gespeichert:

* .env
* Datenbanken
* Backups
* Logdateien
* Vereinsdaten

---

# Projektphilosophie

Die Kegelkasse wird als Hobby- und Community-Projekt entwickelt.

Ziel ist es, Vereinen eine kostenlose und selbst hostbare Lösung bereitzustellen.

Langfristig ist eine freie Community Edition geplant. Freiwillige Unterstützung, Feedback und Verbesserungsvorschläge sind willkommen.

---

# Lizenz

Noch nicht festgelegt.

Bis zur Veröffentlichung verbleiben alle Rechte beim Projektinhaber.

---

# Autor

aluecke75

Entwickelt für den praktischen Einsatz im Kegelverein.
