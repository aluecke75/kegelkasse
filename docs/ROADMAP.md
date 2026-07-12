# Roadmap

Stand: 2026-07-12. Aktueller Gesamtstatus: siehe [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Priorisierte offene Arbeiten (Vereinsversion)

1. **Revisionsprotokoll** (höchste Priorität) — ✅ im Wesentlichen abgeschlossen
   - ✅ Vorher-/Nachher-Werte: zeigt jetzt nur noch geänderte Felder als kompakte Tabelle (2026-07-09)
   - ✅ Filter optimiert: Datumsfilter (von/bis) sowie Deep-Link-Filterung auf einzelne Datensätze (z. B. "alle Änderungen zu diesem Mitglied/Kegelabend"), mit Links von der Mitglied- und Kegelabend-Seite (2026-07-09)
   - Ereignisse bereinigen — noch offen, genaue Anforderung noch zu klären
   - Darstellung modernisieren — im Wesentlichen bereits modern, keine weiteren Punkte bekannt

2. **Mobile Optimierung**
   - Stand 2026-07-09: keine konkreten Probleme bekannt, Nutzer aktuell zufrieden
   - Feinschliff der Tabellen, Live-Updates, Scrollverhalten, Performance, kleine Darstellungsfehler — bei Bedarf erneut aufgreifen, sobald konkrete Probleme auftreten

3. **Mein Kegelabend** — ✅ bereits vollständig umgesetzt, am 2026-07-09 geprüft und bestätigt
   - Live-Aktualisierung während des laufenden Abends (Polling alle 4 Sekunden)
   - getrennte Anzeige "geworfen" / "bezahlen" bei Strafarten wie "Alle Neune"/"Kranz"
   - Strafkonto & aktueller Abend als Karten am Seitenende — Platzierung vom Nutzer bestätigt, keine Änderung gewünscht

4. **Zinsmodul** — ✅ abgeschlossen (2026-07-09)
   - Oberfläche geprüft: bereits vollständig (Einstellung, Buchung mit Live-Steuervorschau, Verlauf, Stornieren)
   - Tests durchgeführt: Fehler gefunden und behoben — die Rundungseinstellung (Kaufmännisch/Aufrunden/Abrunden) wurde bei der Steuerberechnung bisher ignoriert
   - Berichte ergänzt: Zinsstatistik in den Auswertungen, Aufschlüsselung im Jahresabschluss, Export (PDF/Excel/CSV) für die Steuererklärung

5. **Kassenprüfung** — ✅ abgeschlossen (2026-07-09)
   - Abschlussworkflow, Bestätigung (mit Passwort-Bestätigung durch Kassenprüfer/-in) und Prüfprotokoll waren schon vollständig
   - Export ergänzt (PDF/Excel/CSV), analog zu den anderen Bereichen

6. **Jahresabschluss** — ✅ abgeschlossen (2026-07-09)
   - Archivierung und Abschlussprotokoll waren bereits vollständig
   - Saldenübernahme: Schreibsperre für abgeschlossene Jahre ergänzt (Kassenbuch, Zinsen, Kegelabend-Abrechnung), mit bewusstem Admin-Override
   - Jahresberichte: neuer, übersichtlich gestalteter PDF/Excel/CSV-Export mit den wichtigsten Jahreszahlen
   - Jahresabschluss-Assistent: bewusst zurückgestellt — die einzelne Vorschau-Seite mit Prüfung auf offene Kegelabende reicht aktuell aus; bei Bedarf später zu einem mehrstufigen Assistenten ausbauen

7. **Statistiken** — ✅ abgeschlossen (2026-07-09)
   - Export war bereits vollständig
   - Diagramme & Trends: Strafgeld pro Monat, Anwesenheit/Fehlzeiten pro Monat (neues, abhängigkeitsfreies SVG-Diagramm-Modul)
   - Jahresvergleiche: Einnahmen/Ausgaben und Kassenbestand-Verlauf je Jahr als Diagramm
   - Individuelle Auswertungen: neue Seite "Meine Statistik" – Mitglieder sehen ihre eigene Entwicklung über die Jahre

8. **PDF-Export** — ✅ abgeschlossen (2026-07-09)
   - Umlaute waren bereits korrekt (cp1252-Kodierung) — bestätigt getestet
   - Seitenköpfe gab es schon; Fußzeilen neu ergänzt
   - Modernes Layout/Tabellenoptik: zweispaltiges Feld/Wert-Layout statt Fließtext
   - Vereinslogo: Admin kann im Adminbereich selbst ein Logo hochladen (PNG mit Transparenz oder JPEG), erscheint automatisch in allen PDF-Exporten

9. **Dokumentenverwaltung** — ✅ bereits vollständig, am 2026-07-09 geprüft und bestätigt
   - Upload (Titel, Kategorie, Datum, Beschreibung), Verknüpfung mit Kegelabend oder Kassenbuch-Eintrag, Vorschau (bild-/PDF-/textbasiert je nach Typ), Download, Archivieren/Wiederherstellen, Suche/Filter — alles mit echten Daten getestet (18 vorhandene Dokumente)

10. **Backup-Modul** — ✅ abgeschlossen (2026-07-09), seither weiter ausgebaut
    - "Backup jetzt", Wiederherstellung (mit automatischer Sicherheitskopie vorher + Bestätigungscode) und Zeitplanung (täglich/wöchentlich/monatlich mit Aufbewahrungsregeln) waren bereits vollständig
    - Externe Ziele: WebDAV/Nextcloud war schon fertig (deckt auch NAS-Systeme ab, z. B. Synology). Dropbox, Google Drive und OneDrive per OAuth2 angebunden (App-Key/Secret bzw. Client-ID/Secret durch Admin selbst hinterlegbar, Verbinden/Trennen per Klick, automatischer Token-Refresh) — "Verbinden" speichert seit v0.99.7 automatisch mit, vorher gingen unsaved Zugangsdaten dabei verloren. Echter Verbindungstest mit realen Dropbox/Google/Microsoft-Zugangsdaten steht weiterhin aus (der Upload-Pfad selbst wurde in v0.99.10 aber ausführlich gegen einen lokalen Test-Server verifiziert).
    - **Neu (v0.99.10):** Verschlüsselung der Cloud-Backup-Kopie, Admin wählt zwischen Schlüsselpaar (kein Passwort nötig) oder Passwort. Lokale Sicherungen bleiben bewusst unverschlüsselt.
    - **Neu (v0.99.8):** druckbares Kegelabend-Protokoll (PDF) als Papier-Rückfallebene für einen einzelnen abgeschlossenen Abend.

11. **Öffentliche Version** (nach Vereinsversion) — mehrere Bausteine existieren durch die Vereinsversion inzwischen bereits, verbleibender Aufwand dadurch kleiner als die Liste suggeriert:
    - Einrichtungsassistent — ✅ existiert bereits (`/setup`), noch nicht für Mehrmandantenbetrieb gedacht
    - Branding (Logo, Farben) — ✅ Logo-Upload + admin-konfigurierbare Anzeige existieren bereits (v0.99.5); Farben noch offen
    - E-Mail-Konfiguration (SMTP) — ✅ existiert bereits (Administration → SMTP-Einstellungen)
    - Admin ohne Vereinsmitgliedschaft — noch offen
    - Import-/Export-Assistent — Vereins-Export/-Import existiert bereits, "Assistent"-Charakter (mehrstufig geführt) noch offen
    - Testmodus mit Demo-Datenbank — ✅ existiert bereits (Test-Datenbank-Umschalter)
    - Cloud-Backups — ✅ existiert bereits, seit v0.99.10 sogar mit optionaler Verschlüsselung
    - Rechteverwaltung — Rollenmodell existiert, Mehrmandanten-/Fremdadmin-Aspekt noch offen
    - Dokumentation — noch offen

Siehe auch [PUBLIC_RELEASE_TODO.md](../PUBLIC_RELEASE_TODO.md) für die "Muss vor 1.0"-Kurzliste.

## Ideen-Backlog (2026-07-12, noch nicht geplant)

Inspiriert durch einen Funktionsvergleich mit einem anderen, technisch komplett anders aufgebauten Kegelverein-Projekt (React/TypeScript statt Python/Flask, kein Code übernommen, nur Funktionsideen — das fremde Repo hat keine Lizenz, Code-Übernahme wäre ohnehin nicht erlaubt gewesen). Noch nicht priorisiert, nur vorgemerkt:

- **Vereinsnachrichten/Ankündigungen-Board** — Stelle, an der der Vorstand Nachrichten fürs ganze Team posten kann, optional mit einfachen Umfragen (z. B. Terminfindung). Größte echte Lücke, überschaubarer Aufwand.
- **Allgemeine Vereinstermine, getrennt vom Kegelabend** — leichte "Termine"-Funktion (Weihnachtsfeier, Mitgliederversammlung, Ausflug) mit einfacher Zu-/Absage, ohne die Finanz-Verknüpfung, die "Kegelabend" hat.
- **Passwortloses Login (Passkey/WebAuthn)** — kein Passwort merken nötig. Nice-to-have, für die kleine Nutzerzahl nicht dringend.
- **Web-Push-Benachrichtigungen** — hängt technisch an der weiter unten bereits zurückgestellten PWA/Offline-Funktion, ändert nichts an deren Priorität.

## Bewusst zurückgestellt (nach Version 1.0)

- Unterstützung für MariaDB und PostgreSQL
- Mehrmandanten-/Mehrvereinsbetrieb
- Automatischer Bankabgleich (nur Buchungsvorschläge, keine automatische Verbuchung)
- Frei konfigurierbare Dashboards per Drag & Drop
- Tiefe Integration von Zahlungsanbietern
- Integration mit Paperless-ngx für Dokumentenverknüpfung
- Progressive Web App (PWA) mit Offline-Unterstützung
