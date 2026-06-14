# Kegelkasse

Selbst gehostete Kegelkasse für Kegelvereine.

## Projektstatus

Aktive Entwicklung.

Das Projekt wird aktuell für einen realen Kegelverein entwickelt und befindet sich noch nicht im finalen Produktivstatus.

## Bereits vorhanden

* Benutzeranmeldung
* Rollenverwaltung
* Mitgliederverwaltung
* Startbestände (Kasse / Bank)
* Grundgerüst für Kegelabende
* Docker-Deployment
* SQLite-Datenbank

## Geplante Funktionen

* Vollständige Kegelabend-Erfassung
* Strafenkatalog
* Gästeverwaltung
* Bahnkosten-Verwaltung
* Kontoübersicht
* Auswertungen und Statistiken
* Pumpenkönig / Pumpenkönigin
* Kränze
* Fehltage
* Einzahlungen und Auszahlungen
* Zinsberechnung für Sparkonto
* Rollen- und Rechteverwaltung
* Schreibsperre bei gleichzeitiger Bearbeitung

## Docker

Projektverzeichnis:

```text
/volume1/docker/kegelkasse
```

Standardport:

```text
8091
```

## Sicherheit

Folgende Dateien werden bewusst nicht in Git gespeichert:

* .env
* Datenbanken
* Backups
* Logdateien

## Lizenz

Derzeit keine Lizenz festgelegt.

## Autor

Privates Hobbyprojekt.
