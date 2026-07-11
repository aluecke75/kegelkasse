# Changelog

## v0.99.5

- Vereinslogo: Admin kann jetzt einstellen, ob das hochgeladene Logo (zusätzlich zu PDF-Exporten) auch in der App angezeigt wird - aus, in der Kopfzeile auf allen Seiten, oder in der Kopfzeile und groß auf der Anmeldeseite (Administration → Vereinslogo). Standardmäßig aus, keine Änderung am bisherigen Verhalten ohne Admin-Aktion.

## v0.99.4

- Bahnkosten-Eingabefeld: der vorgeschlagene Wert wird jetzt beim ersten Reinklicken automatisch geleert, statt manuell markiert/gelöscht werden zu müssen
- Kegelabend-Abschluss: die Bestätigungsseite "Kegelabend abgeschlossen" nach dem Erfassen der Bahnkosten kann jetzt in den Admin-Einstellungen ein-/ausgeschaltet werden (Administration → Kegelabend-Ablauf); standardmäßig aus, man landet direkt wieder bei der Kegelabend-Übersicht
- Datensicherung: bei "Vorhandene Sicherungen" können jetzt mehrere Sicherungen ausgewählt und gemeinsam gelöscht werden

## v0.99.3

- Internes Aufräumen: app.py (früher ~9100 Zeilen) in über 20 kleinere, nach Fachbereich benannte Dateien aufgeteilt (services/ für Hilfsfunktionen, routes/ für die einzelnen Programmbereiche wie Kegelabende, Kassenbuch, Zinsen, Jahresabschluss, Datensicherung, Berichte). Keine sichtbaren Änderungen für Nutzer - jeder Schritt wurde einzeln gegen die laufende Vereinsdatenbank getestet (alle Seiten, zusätzlich vollständige End-to-End-Tests für Kegelabend-Ablauf, Kassenbuch, Zinsen, Backups, Vereins-Export).

## v0.99.2

- Datensicherung: Status-Anzeige zeigt jetzt korrekt Fehler an (vorher wurde bei jedem gespeicherten Ergebnis fälschlich "Backup erfolgreich" grün angezeigt, auch wenn z. B. die Kopie zu Dropbox fehlgeschlagen war)
- Datensicherung: Verbindungsstatus zum zusätzlichen Sicherungsziel (Dropbox/Google Drive/OneDrive) wird jetzt deutlich sichtbar gemacht (verbunden/nicht verbunden)
- Datensicherung: ein fehlgeschlagener Verbindungstest wird jetzt ebenfalls im Status gespeichert, nicht nur bei Erfolg

## v0.99.1

- Globale Suche um das Revisionsprotokoll erweitert
- Kassenprüfungs-Erinnerung im Dashboard

## v0.99.0

Große Sammel-Version: praktisch alle priorisierten Restarbeiten vor Version 1.0 abgeschlossen.

- Revisionsprotokoll: Vorher/Nachher zeigt nur noch geänderte Felder, Datumsfilter, Verlauf zu einem einzelnen Mitglied/Kegelabend
- Zinsmodul: Rundungseinstellung wird bei der Steuerberechnung jetzt korrekt berücksichtigt, neue Zinsstatistik/-berichte
- Kassenprüfung und Zinsen: neue Exporte (PDF/Excel/CSV)
- Jahresabschluss: Schreibsperre für abgeschlossene Jahre, neuer übersichtlicher Jahresbericht-Export
- Statistiken: neue Diagramme (Trends, Jahresvergleiche), neue Seite "Meine Statistik"
- PDF-Export: Fußzeilen, moderneres Layout, eigenes Vereinslogo hochladbar
- Datensicherung: Dropbox, Google Drive und OneDrive als externe Ziele angebunden
- Dashboard: Admin kann einzelne Karten ein-/ausblenden
- Feinschliff: Akkordeon-Bereiche mit gemerktem Zustand, Menü-Dopplungen behoben, Erledigungs-Badge, neue globale Suche

## v0.98.36

- Administration-Menü aufgeräumt.
- `Datensicherung` enthält nur noch Backup, Restore, automatische Sicherungen und Backup-Ziele.
- Neuer Bereich `Import & Export` für Vereins-Export, Vereins-Import und Auswertungs-Exporte.
- Vereins-Import/Export aus der Datensicherung herausgelöst.
