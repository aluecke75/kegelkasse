# Kegelkasse – Offene Punkte (laufend gepflegt)

Diese Datei sammelt den Stand aus den Chat-Zusammenfassungen der
Fehlerbehebungs-Sitzung(en), damit nichts verloren geht, auch wenn der
Chatverlauf mal nicht mehr greifbar ist. Ausgangspunkt war die Prüfliste in
`FEHLERPRUEFUNG_2026-09-14.md` (27 Punkte F1-F12, S1-S10, T1-T5, M1-M5).

Wird bei künftigen Zusammenfassungen um neue offene Punkte ergänzt statt neu
geschrieben — bitte Häkchen setzen/History unten stehen lassen.

## Aktueller Gesamtstand: 27 von 27 Punkten bearbeitet (25 behoben, 2 bewusst offen)

## Aus Zusammenfassung 1 (2026-09-14, nach Rückfragen zu S2/S8 + F10)

- [x] **F10** – Zinsbuchung: Dublettenschutz erkennt jetzt auch überlappende
  (nicht nur exakt identische) Zinsperioden. Commit `ff28956`.
- [x] **S2** – Testdatenbank vor Login umschalten bleibt möglich (bewusst
  genutzter Admin-Workflow laut Rückfrage), verlangt aber jetzt eine
  Admin-Anmeldung (Benutzername/Passwort inline geprüft, inkl.
  Login-Lockout) statt komplett offen zu sein. Der Banner-Button "Zurück zur
  echten Vereinsdatenbank" bleibt bewusst ungeschützt (führt nur zurück in
  den sicheren Normalzustand, zeigt/löscht nichts). Commit `1589eaa`.
- [x] **S8** – `SESSION_COOKIE_SECURE` als neue Einstellung ergänzt,
  **standardmäßig aus**. Laut Rückfrage läuft HTTPS über einen Reverse-Proxy,
  aber `compose.yml` exponiert den Port zusätzlich direkt (`APP_PORT`) —
  daher Opt-in statt automatisch scharf geschaltet. Commit `1589eaa`.
  - [ ] **Noch zu erledigen (nicht von mir gemacht, bewusst offen gelassen):**
    In der echten `.env` auf dem NAS `SESSION_COOKIE_SECURE=true` ergänzen —
    aber nur, falls wirklich **jeder** Zugriff (auch im LAN) über HTTPS
    läuft, sonst sperrt das den Login per direktem `http://<nas-ip>:8091`.
- Nach diesem Stand: 23 von 27 Punkten behoben.

## Aus Zusammenfassung 2 (2026-09-14, nach T3 + F9)

- [x] **T3** – E-Mail-Format wird jetzt beim Speichern geprüft
  (`services/validation.validate_email_format`): Mitglied neu anlegen,
  Mitglied bearbeiten, eigene Konto-E-Mail ändern. Bereits gespeicherte
  Altdaten werden nicht rückwirkend geprüft. Commit `bf86843`.
- [x] **F9** – Ein Sollbetrag von 0 € bei den Monatsbeiträgen (z. B.
  Ehrenmitglied) gilt jetzt als trivial erfüllt statt den Monatsabschluss für
  diesen Monat dauerhaft zu blockieren. Commit `bf86843`.
- Nach diesem Stand: 25 von 27 Punkten behoben. Erneut gegen isolierte
  Demo-Datenbank-Kopie getestet: 81/81 Prüfungen grün, keine Regression.

## Aus Zusammenfassung 3 (2026-09-15, nach M4 + M5, Prüfung F11/T5)

- [x] **M5** – Aktions-Buttons in der Monatsbeiträge-Tabelle vergrößert
  (`.small-button`: `min-height:38px`, mehr Padding statt knapp 30px).
- [x] **M4** – Diagramme (`static/js/charts.js`) reagieren jetzt zusätzlich
  auf Antippen (`touchstart`) statt nur auf Maus-Hover, inkl. Ausblenden des
  Tooltips beim Antippen woanders auf der Seite.
- [ ] **F11 geprüft, weiterhin bewusst offen:** `CashbookEntry` (Kassenbuch)
  und `MemberPenaltyTransaction` (Strafkonto) haben aktuell KEINE gemeinsame
  ID, über die man sie beim Stornieren eindeutig verknüpfen könnte. Ein
  echter Fix bräuchte eine neue Spalte + Schema-Migration (wie seinerzeit
  `source_document_id` beim CSV-Import) UND würde bereits bestehende, schon
  gebuchte Einträge nicht rückwirkend verknüpfen können. Kein Quick-Fix.
- [ ] **T5 nicht angefasst:** Ein Fix müsste auch klären, was eine Seite
  ohne automatisch angelegte Teilnehmer für eine Rolle wie "auditor"
  anzeigt (sonst wirkt die Seite kaputt statt nur "sauberer"). Geringer
  Nutzen für den nötigen Aufwand, daher bewusst nicht angefasst.
- Nach diesem Stand: 25 von 27 behoben, F11 und T5 bewusst offen gelassen
  (technische Begründung s.o., keine reine "keine Zeit gehabt"-Vertagung).

## Nutzer-Feedback 2026-09-16: S2 grundsätzlich überdenken

Rückmeldung nach Test auf der Demo-Instanz: Da `kegelkasse` (Port 8091) und
`kegelkasse-demo` (Port 8092) bereits als getrennte Container laufen, ist die
eingebaute "Testdatenbank vor dem Login umschalten"-Funktion (`/test-database`,
`switch_database_profile`, `test_database_control`, `DEVELOPER_MODE`)
eigentlich überflüssig geworden — zum Testen wird ohnehin einfach der andere
Port benutzt. Statt sie (wie am 2026-09-15 geschehen) nur zusätzlich mit
einer Admin-Anmeldung abzusichern, könnte sie beim nächsten Aufräumen
komplett entfernt werden (kleinere Angriffsfläche als jede Absicherung).
Nicht mehr umgesetzt, um den für heute geplanten Produktiv-Deploy nicht zu
verzögern.

- [x] **Erledigt (2026-09-17, v0.99.24):** Testdatenbank-Umschaltung
  (`/test-database` und zugehörige Routen/Templates/`DEVELOPER_MODE`)
  komplett entfernt, da durch den separaten Demo-Container ersetzt.

## Weiterhin offen: S5

- [ ] **S5 – CSRF-Schutz fehlt projektweit.** Größere, projektweite Änderung
  (Formulare in ~30 Templates betroffen), zu invasiv für eine schnelle
  Zwischenrunde ohne dedizierten Test. Sollte in einer eigenen Sitzung mit
  ausreichend Zeit zum gründlichen Durchtesten gemacht werden.

## Betrieblich noch offen

- [ ] **Deployment auf den Produktiv-Container.** Alle bisherigen Fixes sind
  nur lokal committet und zu Gitea/GitHub gepusht — der laufende
  Produktiv-Container nutzt weiterhin den alten Code. Erst nach expliziter
  Freigabe bauen/deployen (neues Release, GHCR-Image, `docker compose pull
  && docker compose up -d` bzw. `--build`).
- [ ] Eigenes Gegentesten am Handy für **M1** (öffnen sich die
  Hauptmenüs jetzt wirklich per Tippen? — siehe `FEHLERPRUEFUNG_2026-09-14.md`).
- [ ] Prüfen, ob der neue Admin-Anmeldungs-Zwischenschritt bei **S2** so zum
  gewohnten Arbeitsablauf passt.

## Hinweis zu den Sitzungskosten

Die Fehlerbehebungs-Sitzung wurde inzwischen sehr kostenintensiv (viele
Einzeländerungen, jeweils mit Sicherheitsnachfragen). Empfehlung: die
verbleibenden Punkte (S5, F11, T5, M4, M5) in einer **neuen Sitzung**
angehen statt in derselben unbegrenzt weiterzumachen — inhaltlich macht das
keinen Unterschied, spart aber unnötige Kosten.
