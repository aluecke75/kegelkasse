# Datenbank-Migrationen

SQLite bleibt Standard.

Aktueller Stand:

- App-Version: 0.98.4
- DB-Schema-Version: 2026.06.20-1

Aktuell werden kleinere SQLite-Erweiterungen noch in `migrate_schema_extensions()` erledigt.
Für die öffentliche Version sollte daraus später ein geordnetes Migrationssystem entstehen:

1. installierte DB-Version aus `app_settings.db_schema_version` lesen
2. fehlende Migrationen in Reihenfolge ausführen
3. nach erfolgreichem Lauf neue DB-Version speichern
4. Fehler sauber abbrechen und vorheriges Backup empfehlen

Wichtig: Vor jeder Migration muss automatisch oder manuell ein Backup der SQLite-Datenbank erstellt werden.
