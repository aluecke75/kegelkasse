# Changelog

## v0.99.33

- **Grundeinstellungen: neue Reihenfolge der Karten.** "Gastgebühr" und "Gast: Strafen zusätzlich
  zur Gastgebühr?" stehen jetzt (in dieser Reihenfolge) zwischen "Fehlen unentschuldigt" und
  "Bahnkosten Standard"; die Gast-Regel steht nicht mehr am Ende der Seite. Nur die Anzeige
  ändert sich, Werte und Berechnung bleiben unverändert.
- Testskript: neue Prüfung der Kartenreihenfolge.

## v0.99.32

- **Gast-Regel wirkte nicht:** Wurde die Regel "Gast: Strafen zusätzlich zur Gastgebühr?" am
  selben Tag wie ein bestehender Eintrag neu angelegt (z. B. der automatische Startwert),
  entschied bei gleichem Gültigkeitsdatum ein beliebiger Eintrag - der neue Wert "Nein" blieb
  wirkungslos. Jetzt gilt bei gleichem Datum immer der zuletzt angelegte Eintrag. Dasselbe gilt
  nun auch für die Euro-Werte (Gastgebühr, Beiträge, Strafen usw.) und die Anzeige "Aktuelle Werte".
- **Gast-Regel wird wie die anderen Werte angelegt:** Über "+ Neuen Wert anlegen" in den
  Grundeinstellungen lässt sich jetzt "Gast: Strafen zusätzlich zur Gastgebühr?" auswählen; statt
  eines Euro-Betrags erscheint dann die Auswahl Ja/Nein. Der zusätzliche Knopf "Neue Regel für
  Gast-Strafen anlegen" und die zugehörige Seite entfallen.
- Testskript: neue Prüfungen für die Gast-Regel (Formular, Gleichstand beim Datum, Berechnung
  für Gäste mit "Ja" und "Nein").

## v0.99.31

- **Gastkegler: Barzahlung wird jetzt korrekt verbucht.** Gäste tauchten im Schritt
  "Barzahlungen erfassen" gar nicht auf (Template und Verbuchung filterten hart auf
  Mitglieder), wodurch Gastgebühr und Strafen eines Gastes nirgends in Barkasse,
  Kassenbuch oder Auswertungen ankamen. Gäste erscheinen jetzt in der Barzahlungs-Tabelle,
  müssen den fälligen Betrag vollständig bar bezahlen (kein Strafkonto für Gäste), und die
  Zahlung wird korrekt gebucht.
- **Neu:** In den Beitragseinstellungen kann jetzt festgelegt werden, ob Gäste neben der
  festen Gastgebühr auch die Strafen des Abends zahlen (datumsversioniert, wie
  Gastgebühr/Grundbeiträge). Voreingestellt ist "Ja" (bisheriges Verhalten), für vergangene
  Abende ändert sich dadurch nichts.

## v0.99.30

- **Gleichstand zeigt jetzt einen geteilten Platz:** Bei "Pumpenkönig", "Kranzkönig" und der
  Fehltage-Rangliste (persönliche Dashboard-Karte "Meine Statistik" und /reports) wurde bei
  Punktgleichheit einfach durchgezählt (z. B. 1./2./3./4.), obwohl mehrere Mitglieder denselben
  Wert hatten. Jetzt bekommen alle Gleichstand-Fälle denselben (geteilten) Platz, der nächste
  Platz wird korrekt übersprungen (z. B. 1,1,1,4 statt 1,2,3,4). Betraf konkret die eigene
  Fehltage-Rang-Anzeige mehrerer Mitglieder in der laufenden Saison.
- **Pumpen/Kränze zählen nicht mehr für entschuldigt/unentschuldigt fehlende Teilnehmer:** Wurde
  für jemanden zuerst eine Strafanzahl eingetragen und der Status danach auf "fehlt" geändert,
  blieb die Anzahl gespeichert und zählte fälschlich im eigenen "Pumpen"/"Kränze"-Zähler mit -
  obwohl dafür korrekt kein Geld berechnet wurde. Die Zählung filtert jetzt wie die
  Geldberechnung nur noch anwesende Teilnehmer und Gäste.
- Das persönliche "Offene Strafen"/Guthaben auf dem Dashboard wurde geprüft (Gegenrechnung gegen
  alle Buchungskategorien) und ist bereits korrekt - keine Änderung nötig.
- Testskript: zwei neue Checks für die geteilten Ränge und die Zählstrafen-Korrektur.

## v0.99.29

- **"Meine Statistik" jetzt für jede Rolle sichtbar:** Die Dashboard-Karte
  "Meine Statistik" mit dem eigenen offenen Strafkonto bzw. Guthaben wurde
  bisher nur bei Rolle "Mitglied" angezeigt. Admins, Kassierer/-innen und
  Kassenprüfer/-innen, die selbst als Vereinsmitglied verknüpft sind, sahen
  ihren eigenen Saldo nirgends auf dem Dashboard, obwohl er korrekt berechnet
  wurde. Die Karte erscheint jetzt für jeden angemeldeten Nutzer mit
  verknüpftem Mitgliedsprofil, unabhängig von der Rolle.

## v0.99.28

- **Ansehen legt keine Daten mehr an (T5):** Beim Öffnen eines Kegelabends
  ohne Teilnehmer wurden bisher automatisch alle aktiven Mitglieder als
  "anwesend" angelegt und gespeichert - auch für abgeschlossene Abende und
  auch beim Ansehen durch den Kassenprüfer (reine Leserolle). Das passiert
  jetzt nur noch als Fallback für einen **offenen** Abend und nicht mehr für
  die Rolle Kassenprüfer. Im Normalbetrieb ändert sich nichts, weil neue
  Abende ihre Teilnehmer schon beim Anlegen bekommen; in der Produktiv-
  Datenbank war der Pfad nicht betroffen. Schutz vor einem theoretischen
  Fall: ein importierter, abgeschlossener Altabend ohne Teilnehmer hätte
  beim Öffnen rückwirkend alle Mitglieder als anwesend bekommen und die
  Anwesenheitsstatistik verfälscht.
- Testskript: neuer Check für diesen Ablauf.

## v0.99.27

- **Kassenbuch-Storno bucht das Strafkonto zurück (F11):** Wird eine
  automatisch aus der Kegelabend-Abrechnung erzeugte Buchung "Barzahlung
  Strafen" im Kassenbuch storniert, führt das Strafkonto des Mitglieds die
  Zahlung wieder als offen (neue Buchung "Storno Barzahlung", Kategorie
  `cash_payment_void`). Vorher sank nur der Kassenstand, das Mitglied galt
  weiter als "bezahlt". Die Zahlungssummen in den Auswertungen rechnen
  Zahlung und Storno gegeneinander auf.
- Dafür gibt es eine neue Spalte `cashbook_entries.penalty_transaction_id`
  (wird beim Start automatisch angelegt). **Nur neue Abrechnungen** werden
  verknüpft: Bei älteren "Barzahlung Strafen"-Einträgen kann nichts
  automatisch zurückgebucht werden, das Storno zeigt dann einen deutlichen
  Hinweis, das Strafkonto manuell zu prüfen. Passt die verknüpfte
  Strafkonto-Buchung nicht mehr (z. B. nach erneuter Abrechnung), wird
  ebenfalls nichts zurückgebucht, sondern gewarnt.
- Testskript: neuer Check für diesen Ablauf, veralteter Hinweis auf die
  entfernte Testdatenbank-Umschaltung in der Abbruchmeldung korrigiert.

## v0.99.26

- Dashboard: Bei einem aktiven Kegelabend zeigt die Karte "Aktiver Kegelabend"
  jetzt zusätzlich den Countdown zu diesem Abend ("Heute" bzw. "in N Tagen"),
  statt nur Datum und Status-Hinweis. Es wird bewusst nur der Countdown des
  aktiven Abends angezeigt, nicht der Termin danach.

## v0.99.25

- **Regression aus v0.99.23 behoben:** "Aktiver Kegelabend" wird wieder
  unabhängig vom Datum erkannt (nur noch nach Status open/settlement/
  lane_cost). Der in v0.99.23 eingeführte Datumsfilter hatte einen
  alltäglichen Arbeitsablauf gebrochen - Kegelabende werden oft Tage vorher
  angelegt, um bereits bekannte Abmeldungen einzutragen, und sollen dabei
  weiterhin als "Aktiver Kegelabend" mit Status-Hinweis erscheinen statt als
  normaler Termin mit Countdown zum übernächsten Abend.

## v0.99.24

- Die versteckte Testdatenbank-Umschaltung (Logo oben links, Testmodus-Banner
  mit "Zurück zur echten Vereinsdatenbank") wurde komplett entfernt. Sie war
  seit Einführung des separaten Demo-Containers (Port 8092) redundant und
  eine unnötige Angriffsfläche. Die Demo-/Testdatenbank ist jetzt
  ausschließlich über den eigenen Port erreichbar; ein Umschalten zwischen
  Produktiv- und Testdatenbank innerhalb eines laufenden Containers ist
  nicht mehr möglich. Der Einrichtungsassistent für neue Installationen
  ("Demo ausprobieren" bei der Ersteinrichtung) ist davon nicht betroffen.

## v0.99.23

Größere Fehlerprüfung (technisch, fachlich, Sicherheit, Mobile-Bedienung),
Details in `FEHLERPRUEFUNG_2026-09-14.md`/`OFFENE_PUNKTE.md`. 25 von 27
gefundenen Punkten behoben:

- **Sicherheit:** Rollen-Eskalation über das Mitgliederformular geschlossen
  (ein Kassierer-Konto konnte sich selbst zum Admin machen); Testdatenbank-
  Umschalten vor dem Login verlangt jetzt eine Admin-Anmeldung statt komplett
  offen zu sein; gespeichertes XSS über als "Dokument" hochgeladene
  HTML-Dateien geschlossen (Download statt Inline-Vorschau); `/reports`
  zeigte bislang jedem Mitglied die Finanzdaten aller anderen an, jetzt nur
  noch Admin/Kassierer/Kassenprüfer; einfacher Login-Lockout gegen
  Passwort-Raten; CSV-/Formel-Injection-Schutz im Export; optionales
  `SESSION_COOKIE_SECURE`.
- **Fachlich:** Ein im Voraus angelegter, zukünftiger Kegelabend wurde
  überall fälschlich als "gerade laufend" behandelt (verfrühte "Strafen
  erfassen"-Aufforderung, verschwindender Countdown auf dem Dashboard) - an
  allen 4 betroffenen Stellen durch einen Datumsfilter behoben. Zinsen und
  Kassenprüfung rechneten mit dem heutigen statt dem historischen
  Kontostand der jeweiligen Periode. Kegelabend löschen/korrigieren konnte
  durch ein zu ungenaues Textmuster Kassenbuchungen anderer Kegelabende
  mitlöschen. Weitere Korrekturen: Rundungsfehler bei Fehlgeld, fehlende
  Jahresabschluss-Sperren bei Strafgeld/Monatsbeiträgen, hängengebliebene
  Kegelabende blockierten den Jahresabschluss nicht, Startbestände ohne
  Revisionsprotokoll, überlappende Zinsperioden nicht als Dublette erkannt,
  Monatsbeitrag mit 0 € Soll blockierte den Monatsabschluss dauerhaft.
- **Mobile:** Hauptmenüs (Finanzen/Einstellungen/Administration/
  Auswertungen) öffneten sich bisher nur per Maus-Hover und waren auf dem
  Handy vermutlich gar nicht erreichbar - jetzt zusätzlich per Klick/Tipp/
  Tastatur bedienbar. Zähl-Buttons und Tabellen-Buttons für Touch vergrößert,
  Zifferntastatur für das Trefferzahl-Feld, Diagramme reagieren jetzt auch
  auf Antippen.
- **Technisch:** diverses Exception-Handling und Validierungslücken
  geschlossen (doppelter Benutzername, ungültige Formularwerte, E-Mail-
  Format).

## v0.99.21

- Monatsabschluss: neuer Review-Assistent "Buchungen aus Kontoauszug prüfen & übernehmen" (`/finance/monthly-bank-closing/csv-import`, verlinkt vom Monatsabschluss aus) für die zuvor nur als Vorschau nutzbare Kontoauszug-CSV. Jede CSV-Zeile bekommt einen vorausgefüllten Vorschlag für Kategorie/Person mit Konfidenz-Badge (**gelernt** = exakter Treffer aus einer früheren Bestätigung, **vermutet** = Mitgliedsname im Text erkannt, **kein Vorschlag** = manuell auszuwählen), dazu ein rein informativer Dubletten-Hinweis gegen bereits gebuchte Monatsbeiträge. Gebucht wird nur, was bewusst per Checkbox ausgewählt und bestätigt wird; bereits übernommene Zeilen sind bei erneutem Aufruf gesperrt, gesperrte Geschäftsjahre werden automatisch übersprungen. Jede Bestätigung merkt sich die (ggf. korrigierte) Zuordnung für künftige, ähnliche Buchungen (z. B. wiederkehrende Lastschriften mit wechselnder Belegnummer).
- Dabei bei der Durchsicht gefunden und behoben: eine stornierte CSV-Buchung blieb bisher dauerhaft als "bereits übernommen" gesperrt statt nach dem Stornieren erneut zur Übernahme angeboten zu werden; ein doppelt übermittelter Auswahlwert im selben Formular-Abschicken konnte dieselbe CSV-Zeile doppelt buchen.

## v0.99.17

- Dashboard-Fehler behoben: Mitglieder (Rolle "member") sahen die Kachel "Offene Monatsbeiträge" auf dem Dashboard, aber der Link führte auf die Admin-/Kassierer-Seite `/finance/monthly-contributions`, die für ihre Rolle gesperrt ist (403-Fehler). Neue schreibgeschützte Seite "Meine Monatsbeiträge" (`/finance/my-monthly-contributions`) zeigt Mitgliedern jetzt mindestens ihren eigenen Zahlungsstand der letzten 12 Monate (Soll, eingegangen, Wertstellung, Status); die Dashboard-Kachel verlinkt für Mitglieder dorthin.

## v0.99.16

- Datensicherung: zwei echte Fehler im wiederhergestellten Formular für "Automatische Sicherungen"/Cloud-Ziel behoben, gefunden bei einer gezielten UI-Durchsicht und selbst mit echten Anfragen gegen die App nachgestellt:
  - WebDAV-Zieleinstellungen (URL/Benutzer/Passwort) ließen sich über die Oberfläche gar nicht speichern - der Haupt-Button "Einstellungen speichern" hat serverseitig nur die Zeitplan-Felder gespeichert, nie das Cloud-Ziel. Jetzt schickt der Klick zusätzlich im Hintergrund einmal die Cloud-Ziel-Felder mit, unsichtbar für den Nutzer (keine Änderung an Aussehen oder Bedienung).
  - Die Buttons "Mit Dropbox/Google Drive/OneDrive verbinden" und "Ziel testen" haben nie ihre eigentliche Aktion ausgelöst, sondern immer nur die Zeitplan-Einstellungen erneut gespeichert - Ursache war ein verstecktes Formularfeld mit demselben Namen wie die Buttons, dessen Wert beim Absenden immer Vorrang hatte. Behoben, ohne am Aussehen etwas zu ändern.

## v0.99.15

- Datensicherung: eigenes Kegelkasse-Design (Akkordeon-Bereiche, Status-Übersicht, Datenbankgröße/Tabellen/Einträge) wiederhergestellt. Die Umstellung auf das gemeinsame DeveloperKit-Backup-Modul (v0.99.13) hatte die Seite vorübergehend auf dessen generisches, unstyled Basis-Template umgestellt — die Funktionslogik lief unverändert weiter, aber Optik und die aufklappbaren Akkordeon-Abschnitte fehlten. Jetzt über ein eigenes Kegelkasse-Template (`app/templates/backup/uebersicht.html`, überschreibt das DeveloperKit-Template gezielt nur für diese eine Seite) gelöst, ohne die gemeinsame Modul-Architektur wieder aufzugeben.

## v0.99.14

- Beim Deploy von v0.99.13 gegen die echte Vereinsdatenbank gefunden: die bereits vorhandenen 9 Sicherungen (im alten Dateinamensformat) tauchten nach der Umstellung in der neuen Backup-Übersicht nicht mehr auf - über den Dateipfad wären sie zwar weiterhin wiederherstellbar gewesen, aber unsichtbar. Behoben in DeveloperKit v0.1.10 (`DEVELOPERKIT_VERSION` entsprechend angehoben).

## v0.99.13

- **Datensicherung auf das gemeinsame DeveloperKit-Backup-Modul umgestellt.** Die allgemeine Datensicherung (lokale Sicherungen, automatische Zeitplanung, Cloud-Ziele, Verschlüsselung) läuft jetzt über dasselbe, auch von KleinvermieterLotse genutzte Modul statt über eigenständigen Kegelkasse-Code - neu erreichbar unter Administration → Datensicherung (`/verwaltung/backups`, vorher `/backups`). Bestehende Backup-Einstellungen (Zeitplan, Cloud-Zugangsdaten, Verschlüsselung) wurden automatisch übernommen, bereits vorhandene Sicherungs-ZIPs im alten Format bleiben lesbar und wiederherstellbar - kein Datenverlust-Risiko beim Umstieg.
- Der **Vereins-Export/-Import** (Administration → Import/Export, Umzug/Archivierung/Wiederverwendung in der öffentlichen Version) bleibt unverändert eigenständiger Kegelkasse-Code, unabhängig von der allgemeinen Datensicherung.
- Dabei im gemeinsamen Modul gefunden und behoben: eine Wiederherstellung oder ein Vereins-Import legt vorher automatisch eine Sicherheitskopie an - war dabei ein Cloud-Ziel (z.B. Dropbox) konfiguriert, aber gerade nicht erreichbar, wurde die komplette Wiederherstellung/der Import blockiert, obwohl die lokale Sicherheitskopie längst erfolgreich geschrieben war. Betrifft jetzt auch KleinvermieterLotse (siehe DeveloperKit v0.1.9).
- Internes: `tests/run_checks.py` auf den neuen Pfad umgestellt; End-to-End gegen eine echte Kopie der Demo-Datenbank sowie echte, im alten Format erstellte Sicherungs-ZIPs getestet (Rückwärtskompatibilität, Einstellungs-Migration, Vereins-Export/-Import-Rundlauf, Einrichtungsassistent-Wiederherstellung).

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
