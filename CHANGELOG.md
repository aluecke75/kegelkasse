# Changelog

## v0.99.12

- Revisionsprotokoll: neuer Bereich "🧹 Revisionsprotokoll bereinigen" (nur für Admins). Aufbewahrungsfrist wählbar (nie/90 Tage/180 Tage/1/2/5 Jahre) - betrifft ausschließlich rein operative Kategorien (Datensicherung, System, Vereins-Import, Vereins-Export) ohne dauerhaften Beweiswert für eine Kassenprüfung; finanziell/rechtlich relevante Einträge (Finanzen, Kegelabende, Mitglieder, Einstellungen, Strafarten, Jahresabschluss, Dokumente, Zinsen) bleiben davon immer unberührt. Läuft automatisch höchstens einmal täglich im Hintergrund, zusätzlich manuelles Einzel-/Sammel-Löschen einzelner Einträge über Auswahl-Checkboxen möglich. Kategorie-Filter der Übersicht um alle tatsächlich verwendeten Kategorien ergänzt (vorher fehlten z.B. Jahresabschluss/Zinsen als Filter, "Dokumente"/"Datensicherung" stimmten nicht mit den echten Kategorienamen überein).
- Dabei gefunden und behoben: Löschen von Revisionsprotokoll-Einträgen (manuell wie automatisch) konnte in einem Sonderfall wirkungslos bleiben - wurde ausgerechnet der zuletzt angelegte Eintrag der gesamten Tabelle gelöscht, vergab SQLite die frei gewordene ID sofort an den direkt danach geschriebenen "X Einträge gelöscht"-Protokolleintrag selbst weiter, wodurch der gelöschte Eintrag scheinbar weiter existierte (tatsächlich war es ein neuer Eintrag mit zufällig gleicher ID). Behoben, indem der "gelöscht"-Protokolleintrag jetzt immer vor der eigentlichen Löschung geschrieben wird.
- Seiten "Strafen erfassen" und "Kegelabend abgeschlossen" nutzen auf breiten Monitoren (z. B. 16:10) jetzt die volle verfügbare Bildschirmbreite statt einer festen Höchstbreite. Zusätzlich bleibt die horizontale Scrollleiste der Tabelle immer am unteren Bildschirmrand erreichbar, auch bei einer langen Teilnehmerliste - man muss nicht mehr erst ganz nach unten scrollen, um sie zu erreichen.
- Datensicherung: die Einrichtungshinweise zu WebDAV/Dropbox/Google Drive/OneDrive stehen jetzt hinter einem kleinen "ⓘ"-Symbol statt dauerhaft im Weg zu sein. Bei Dropbox/Google Drive zusätzlich ein neuer Hinweis: wurde die Berechtigung nachträglich in der jeweiligen App-Konsole geändert, muss die bestehende Verbindung erst getrennt und neu hergestellt werden, sonst schlägt der Upload weiter fehl (reale Fehlermeldung einer Session mit einer fehlenden `files.content.write`-Berechtigung als Anlass).
- Auswertungen → Export: die Buttons "...exportieren"/"...erstellen" der Export-Kacheln sitzen jetzt bündig unten, unabhängig davon wie viele Felder eine Kachel hat.

## v0.99.11

- Internes: dauerhaftes Testskript `app/tests/run_checks.py` ins Repo aufgenommen (per `docker exec kegelkasse python3 tests/run_checks.py` ausführbar). Prüft neben allen wichtigen Seiten (GET) jetzt auch die wichtigsten schreibenden Abläufe (Konto bearbeiten, Passwort vergessen, Admin-Einstellung ändern, Sicherung erstellen/prüfen/löschen, Kegelabend anlegen/absagen) mit echter Zustandsänderung und räumt sich danach selbst wieder auf. Keine sichtbaren Änderungen für Nutzer - reine Absicherung gegen künftige Regressionen wie den audit_log-Fund in v0.99.9, den ein reiner GET-Test nie gefunden hätte.

## v0.99.10

- Datensicherung: Verschlüsselung für das zusätzliche Cloud-Ziel (Dropbox/WebDAV/Google Drive/OneDrive), auf Nutzerwunsch mit zwei wählbaren Methoden (Administration → Datensicherung → "Verschlüsselung für Cloud-Sicherungen"):
  - **Schlüsselpaar**: kein Passwort nötig. Der private Schlüssel wird beim Erzeugen einmalig zum Download angeboten und nirgends gespeichert; automatische Sicherungen laufen danach ganz ohne weitere Eingabe verschlüsselt weiter.
  - **Passwort**: klassisches, auf dem Server hinterlegtes Passwort.
  - Die **lokale** Sicherung bleibt in beiden Fällen bewusst unverschlüsselt und sofort wiederherstellbar wie bisher - nur die Kopie, die an das externe Ziel hochgeladen wird, ist geschützt. Verliert man Schlüssel oder Passwort, bleiben die lokalen Sicherungen trotzdem uneingeschränkt nutzbar.
  - Neuer Bereich "Verschlüsselte Cloud-Sicherung wiederherstellen" zum Hochladen einer aus der Cloud heruntergeladenen `.enc`-Datei samt privater Schlüsseldatei bzw. Passwort.

## v0.99.9

Aus einer kompletten Code-Durchsicht der heutigen Session gefunden und behoben:

- Kritischer Fehler behoben: Eigenes Konto bearbeiten (Benutzername/E-Mail/Passwort ändern), "Passwort vergessen" und die Ersteinrichtung (/setup) warfen einen Internal Server Error, sobald tatsächlich eine Änderung gespeichert wurde - eine beim Aufteilen von app.py vergessene Import-Zeile fürs Revisionsprotokoll. Betraf nur diese drei Bereiche, alle anderen Seiten waren nicht betroffen.
- Jahresabschluss-Einträge im Revisionsprotokoll und Export zeigten Notiz, Bestätigt-von und Bestätigt-am nicht mehr an (beim Aufteilen von app.py versehentlich weggefallen) - wieder ergänzt.
- Kleine Aufräumarbeiten ohne sichtbare Auswirkung: doppelte Logo-Statusabfrage im Adminbereich entfernt, und "python app.py" direkt starten (falsche Startmethode, die zum in v0.99.6 behobenen Absturz-Bug zurückführen würde) bricht jetzt mit einer klaren Fehlermeldung ab statt einen kaputten Server zu starten.

## v0.99.8

- Auswertungen → Export: neue Kachel "Kegelabend" für ein druckbares Protokoll eines einzelnen abgeschlossenen Kegelabends (Querformat-PDF) - als Papier-Rückfallebene, falls die App mal nicht erreichbar ist. Vorausgewählt ist immer der zuletzt abgeschlossene Abend, ein gerade laufender oder ausgefallener Abend steht nie zur Auswahl. Enthält Teilnehmerliste mit Anwesenheit, Strafen je Strafart, korrekt verrechnete Endsumme je Person (inkl. Fehlgeld/Gastbeitrag/Rundenanteil), Bahnkosten, Strafensumme, tatsächlichen Barkassenstand direkt nach diesem Abend, Vereinslogo und Unterschriftenzeile.

## v0.99.7

- Datensicherung/Cloud-Ziele (Dropbox, Google Drive, OneDrive): App-Key/Client-ID und Secret gingen bisher verloren, wenn man direkt auf "Verbinden" klickte, ohne vorher extra auf "Speichern" zu klicken - die Zugangsdaten wurden nie abgeschickt. "Verbinden" speichert jetzt automatisch mit. Zugangsdaten bleiben außerdem jetzt auch nach einem Fehler erhalten und werden nicht mehr blind geleert.
- Alle Zugangsdaten-Felder bei der Datensicherung (WebDAV-Passwort, Dropbox-/Google-/OneDrive-Secret) zeigen den gespeicherten Wert jetzt mit einem 👁-Symbol an, mit dem man ihn bei Bedarf im Klartext einblenden kann, statt ihn nur "gespeichert, unsichtbar" anzuzeigen.

## v0.99.6

- Kritischer Fehler behoben: Administration, Kassenbuch, Zinsen, Monatsbeiträge und weitere Bereiche konnten im laufenden Betrieb einen "Internal Server Error" werfen. Ursache war ein technisches Detail aus der Code-Struktur-Aufteilung (v0.99.3): der Programmstart lud app.py unter einem anderen internen Namen, sodass es beim ersten Aufruf einer betroffenen Seite nach dem Serverstart intern doppelt geladen wurde. Jetzt wird die App über einen sauberen Startpunkt geladen, der Fehler kann nicht mehr auftreten.

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
