# Roadmap

Stand: 2026-07-09. Aktueller Gesamtstatus: siehe [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Priorisierte offene Arbeiten (Vereinsversion)

1. **Revisionsprotokoll** (höchste Priorität)
   - ✅ Vorher-/Nachher-Werte: zeigt jetzt nur noch geänderte Felder als kompakte Tabelle (2026-07-09)
   - Filter optimieren (Datumsfilter, "alle Änderungen zu Person/Kegelabend X") — noch offen
   - Ereignisse bereinigen — noch offen, genaue Anforderung noch zu klären
   - Darstellung modernisieren — im Wesentlichen bereits modern, keine weiteren Punkte bekannt

2. **Mobile Optimierung**
   - Stand 2026-07-09: keine konkreten Probleme bekannt, Nutzer aktuell zufrieden
   - Feinschliff der Tabellen, Live-Updates, Scrollverhalten, Performance, kleine Darstellungsfehler — bei Bedarf erneut aufgreifen, sobald konkrete Probleme auftreten

3. **Mein Kegelabend** — ✅ bereits vollständig umgesetzt, am 2026-07-09 geprüft und bestätigt
   - Live-Aktualisierung während des laufenden Abends (Polling alle 4 Sekunden)
   - getrennte Anzeige "geworfen" / "bezahlen" bei Strafarten wie "Alle Neune"/"Kranz"
   - Strafkonto & aktueller Abend als Karten am Seitenende — Platzierung vom Nutzer bestätigt, keine Änderung gewünscht

4. **Zinsmodul**
   - restliche Oberfläche
   - Tests
   - Berichte

5. **Kassenprüfung**
   - Abschlussworkflow
   - Bestätigung
   - Export
   - Prüfprotokoll

6. **Jahresabschluss**
   - Jahresabschluss-Assistent
   - Archivierung
   - Jahresberichte
   - Saldenübernahme
   - Abschlussprotokoll

7. **Statistiken**
   - Diagramme
   - Trends
   - Jahresvergleiche
   - individuelle Auswertungen
   - Export

8. **PDF-Export**
   - Umlaute-Bug (z. B. "Höchste" statt "Hˆ¶chste")
   - modernes Layout
   - Tabellenoptik
   - Vereinslogo
   - Seitenköpfe / Fußzeilen

9. **Dokumentenverwaltung**
   - Upload von Rechnungen
   - Verknüpfung mit Buchungen
   - Vorschau
   - Download

10. **Backup-Modul**
    - "Backup jetzt"
    - Wiederherstellung
    - Zeitplanung
    - externe Ziele (WebDAV, SMB/NAS, Nextcloud, Dropbox, Google Drive)

11. **Öffentliche Version** (nach Vereinsversion)
    - Einrichtungsassistent
    - Branding (Logo, Farben)
    - E-Mail-Konfiguration (SMTP)
    - Admin ohne Vereinsmitgliedschaft
    - Import-/Export-Assistent
    - Testmodus mit Demo-Datenbank
    - Cloud-Backups
    - Rechteverwaltung
    - Dokumentation

Siehe auch [PUBLIC_RELEASE_TODO.md](../PUBLIC_RELEASE_TODO.md) für die "Muss vor 1.0"-Kurzliste.

## Bewusst zurückgestellt (nach Version 1.0)

- Unterstützung für MariaDB und PostgreSQL
- Mehrmandanten-/Mehrvereinsbetrieb
- Automatischer Bankabgleich (nur Buchungsvorschläge, keine automatische Verbuchung)
- Frei konfigurierbare Dashboards per Drag & Drop
- Tiefe Integration von Zahlungsanbietern
- Integration mit Paperless-ngx für Dokumentenverknüpfung
- Progressive Web App (PWA) mit Offline-Unterstützung
