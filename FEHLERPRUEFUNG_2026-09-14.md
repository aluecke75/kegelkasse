# Kegelkasse – Fehlerprüfung 2026-09-14

## Update 2026-09-14: Umsetzung

Auf Wunsch wurden die meisten Punkte bereits behoben (siehe Status-Tabelle
unten) und lokal committet, aber **noch nicht deployt** — der laufende
Produktiv-Container nutzt weiter den alten Code, bis Build/Deploy explizit
freigegeben wird. Gegen eine isolierte Kopie der Demo-Datenbank getestet:
bestehende Testsuite `tests/run_checks.py` läuft **81/81** grün (keine
Regression). F1/F2 zusätzlich gezielt mit eigenem Testskript verifiziert.

| # | Status | Kurzfassung |
|---|--------|-------------|
| S1 | ✅ behoben | Rollen-Whitelist + nur Admin darf Rolle "Admin" vergeben |
| S2 | ✅ behoben | `/test-database/switch`+`/control` jetzt Login+Admin-Pflicht |
| S3 | ✅ behoben | HTML-Dokumente: Download statt Inline-Vorschau |
| S4 | ✅ behoben | `/reports` jetzt admin/cashier/auditor-only, Nav-Link versteckt |
| S5 | ⏸️ zurückgestellt | CSRF-Schutz: Änderung wäre projektweit (~30 Templates), zu invasiv für diese Runde ohne dedizierten Test |
| S6 | ✅ behoben | Login-Lockout: 5 Fehlversuche/5 Min. → 60 Sek. Sperre je Benutzername |
| S7 | ✅ behoben | CSV-/Formel-Injection-Schutz in Export (führendes Apostroph bei =+-@) |
| S8 | ✅ behoben (opt-in) | `SESSION_COOKIE_SECURE` als neue Einstellung ergänzt, standardmäßig `false`. Da HTTPS via Reverse-Proxy bestätigt wurde: in der echten `.env` `SESSION_COOKIE_SECURE=true` setzen, um es scharf zu schalten (nur falls WIRKLICH jeder Zugriff, auch im LAN, über HTTPS läuft) |
| S9 | ℹ️ kein Code-Fix nötig | Aktuell nicht ausnutzbar, da echte `.env` bereits starke Werte hat |
| S10 | ℹ️ kein Code-Fix nötig | Keine Nutzereingaben beteiligt, kein echtes Risiko |
| F1 | ✅ behoben | Löschbug: Wortgrenzen-Prüfung statt reinem LIKE-Textmuster |
| F2 | ✅ behoben | "Aktiver Kegelabend" jetzt an allen 4 Stellen datumsgefiltert |
| F3 | ✅ behoben | Zinsberechnung nutzt historischen Kontostand zum Periodenende |
| F4 | ✅ behoben | Kassenprüfung nutzt historischen Kontostand zum Prüfdatum |
| F5 | ✅ behoben | Rundung: immer aufrunden statt Banker's Rounding |
| F6 | ✅ behoben | Strafgeld-Überweisung: Sperre für abgeschlossene Jahre ergänzt |
| F7 | ✅ behoben | Monatsbeiträge: Sperre für abgeschlossene Jahre ergänzt |
| F8 | ✅ behoben | Jahresabschluss blockiert jetzt auch bei "Barzahlung"/"Bahnkosten" hängenden Abenden |
| F9 | ⏸️ zurückgestellt | Bräuchte einen neuen "manuell abschließen"-Weg (kleines Feature, keine reine Fehlerkorrektur) |
| F10 | ✅ behoben | Dublettenschutz erkennt jetzt auch überlappende (nicht nur exakt gleiche) Zinsperioden |
| F11 | ⏸️ zurückgestellt | Storno-Rücksynchronisation zum Strafkonto ist eine größere, funktionsübergreifende Änderung — lieber gezielt einzeln angehen |
| F12 | ✅ behoben | Startbestände: Revisions-Protokolleintrag ergänzt |
| T1 | ✅ behoben | Doppelter Benutzername beim Bearbeiten wird jetzt abgefangen |
| T2 | ✅ behoben | `int()`-Absturz bei manipulierten Formularfeldern abgefangen (admin.py, documents.py) |
| T3 | ⏸️ zurückgestellt | E-Mail-Validierung nachträglich scharf zu schalten könnte bereits gespeicherte Altdaten treffen — erst gezielt prüfen |
| T4 | ✅ behoben | Ungültige Rolle fällt jetzt auf "auditor" statt "admin" zurück |
| T5 | ⏸️ zurückgestellt | Nebenwirkung bei GET ist unschön, aber risikoarm; Änderung erst nach Rücksprache |
| M1 | ✅ behoben | Menüs (Finanzen/Einstellungen/Administration/Auswertungen) jetzt per Klick/Tipp/Tastatur bedienbar, zusätzlich zu Hover |
| M2 | ✅ behoben | Zähl-Buttons auf Mobile auf ~44px vergrößert |
| M3 | ✅ behoben | Trefferzahl-Feld zeigt jetzt die Zifferntastatur auf dem Handy |
| M4 | ⏸️ zurückgestellt | Diagramm-Tooltips ohne Touch: niedrige Priorität, Werte stehen bereits als Tabelle daneben |
| M5 | ⏸️ zurückgestellt | Kleine Buttons in Tabelle: rein kosmetisch, niedrige Priorität |

**Bitte unbedingt selbst gegentesten, insbesondere:**
- M1 am eigenen Handy: öffnen sich die Menüs jetzt per Tippen?
- F3/F4: Bereits gebuchte alte Zinsgutschriften/Kassenprüfungen wurden NICHT
  rückwirkend korrigiert, nur die Berechnung für neue/zukünftige Buchungen.

**Update zu S2** (Rückfrage beantwortet: Vor-Login-Umschalten wird bewusst
als Admin genutzt): Der Weg bleibt erhalten, ist aber jetzt an eine
Admin-Anmeldung geknüpft. `/test-database` fragt ohne bestehende Session
zusätzlich Admin-Benutzername/-Passwort ab (inkl. Lockout wie beim
normalen Login), bevor die Datenbank gewechselt wird. Der Banner-Button
"Zurück zur echten Vereinsdatenbank" bleibt bewusst ungeschützt, da er nur
in den sicheren Normalzustand zurückwechselt und keine Testdaten offenlegt
oder etwas löscht. Reset/Löschen einer Testdatenbank verlangen ebenfalls die
Admin-Anmeldung. Bitte einmal gegentesten, ob der gewohnte Ablauf so noch
passt.

**Update zu S8** (Rückfrage beantwortet: HTTPS via Reverse-Proxy vorhanden):
Die Einstellung existiert jetzt (`SESSION_COOKIE_SECURE`), ist aber bewusst
standardmäßig aus, weil `compose.yml` den Port zusätzlich direkt exponiert
(`APP_PORT`) und ich nicht weiß, ob z.B. im Heimnetz manchmal auch direkt per
`http://<nas-ip>:8091` zugegriffen wird. Bitte in der echten `.env`
`SESSION_COOKIE_SECURE=true` ergänzen, **nur falls wirklich jeder Zugriff**
(auch lokal) über HTTPS läuft — sonst könnte sich niemand mehr per direktem
HTTP-Zugriff einloggen.

---

Diese Datei war ursprünglich eine reine **Fund-Liste zum Durchsehen**. Es
wurde nichts am Code verändert, nichts gefixt und nichts committet außer dem
vorab vereinbarten Backup (siehe unten). Bitte einfach bei jedem Punkt
ankreuzen, was tatsächlich angegangen werden soll — danach setze ich das
gezielt um.

Vorgehen: 3 spezialisierte Agenten (Finanz-/Kernlogik, Sicherheit/Infrastruktur,
Mobile-vs-Desktop-Templates) haben den Code statisch geprüft, ergänzt durch
eigene Recherche inkl. eines Live-Belegs gegen die **Demo-Datenbank** (nie
gegen die echte Vereinsdatenbank). Es wurde nirgends schreibend in echte
Daten eingegriffen.

## Backup (erledigt)

- [x] Vorhandener uncommitteter Arbeitsstand (Testmodus-Banner + Mobile-Strafen-Fix
  in `event_detail.html`) committet und zu **Gitea** und **GitHub** gepusht
  (Commit `a22f227`, Merge `a8d3048`). Beide Remotes sind aktuell synchron.

---

## 1. Fachliche Fehler (Geld/Business-Logik)

### F1 — 🔴 Kegelabend löschen/korrigieren kann Kassenbuchungen ANDERER Abende löschen
**Datei:** `routes/events.py:838-847` (Definition `reset_event_bookings`), Aufrufstellen
`events.py:1147`, `1225`, `1486` · **Schweregrad: hoch**

Buchungen werden über einen Text-Treffer in der Beschreibung gesucht:
`AccountTransaction.description.like(f"%Kegelabend #{event.id}%")`. Sobald es
zweistellige Event-Nummern gibt, ist z. B. `%Kegelabend #1%` auch Teilstring
von „Kegelabend #10“–„#19“, „#100“–„#199“ usw. Wird Abend #1 gelöscht oder
seine Barzahlung erneut verbucht, werden dabei **auch Buchungen anderer
Abende mitgelöscht** — Kassenstand und ggf. bereits erstellte
Jahresabschlüsse werden dauerhaft falsch, ohne jede Fehlermeldung.
- [ ] Prüfen/beheben

### F2 — 🟠 „Aktiver Kegelabend“ wird nicht nach Datum gefiltert (4 Fundorte, deckt deine gemeldeten Dashboard-Probleme ab)
**Schweregrad: mittel, aber Ursache mehrerer von dir gemeldeter Symptome**

Ein im Voraus für ein zukünftiges Datum angelegter Kegelabend bekommt sofort
Status `"open"` (`events.py:983-988`) und wird an mehreren Stellen wie ein
**heute laufender** Abend behandelt, weil nirgends `event_date <= heute`
geprüft wird:
- `app.py:769-774` (`dashboard()`): Karte „Heute zu erledigen“ zeigt fälschlich
  „Strafen erfassen“; `templates/dashboard.html:16` blendet dafür den
  Countdown zum nächsten Kegelabend komplett aus, statt ihn dauerhaft stehen
  zu lassen.
- `app.py:190-204` (`inject_active_event_navigation`): grüner Punkt „Aktiver
  Kegelabend“ im Hauptmenü erscheint auf jeder Seite verfrüht.
- `routes/events.py:656-664` (`personal_event_payload`, „Mein Kegelabend“):
  Mitglieder sehen den zukünftigen Abend als „live“, inkl. automatisch
  vorbelegtem Status „Anwesend“ (`event_new()` Zeile 1001-1008).
- `routes/events.py:916-945`: Kegelabend-Übersicht zeigt ihn verfrüht als
  „aktiv“ und `event_new()` verweigert das Anlegen eines *echten* neuen
  Abends mit „Es gibt bereits einen offenen Kegelabend“.

Das erklärt genau die drei von dir im todo.md notierten Beobachtungen
(verfrühte „Strafen erfassen“-Aufforderung, verschwindender Countdown,
Verwirrung im Dashboard).
- [ ] Prüfen/beheben

### F3 — 🟠 Zinsberechnung nutzt den heutigen Kontostand statt den historischen Stand der Zinsperiode
**Datei:** `routes/interest.py:349, 446` (`basis_balance_cents = account_balance("bank")`)
· vgl. `routes/cashbook.py:15-26` · **Schweregrad: mittel-hoch**

`account_balance()` summiert ALLE Bankbuchungen bis heute (Live-Saldo), nicht
den Stand zum Ende der abzurechnenden Zinsperiode. Es gibt bereits eine
passende Funktion `account_balance_as_of(account, date)`, die hier nicht
verwendet wird. **Mit echten Demo-Daten belegt:** für denselben Zeitraum
01.–30.06. wurde je nach Berechnungszeitpunkt einmal mit 3.500,05 € und
einmal mit 3.502,70 € Basis gerechnet — reproduzierbar falsch, sobald nach
Periodenende weitere Buchungen erfolgen. Das ist vermutlich die Ursache
deines gemeldeten Zinsen-Falsch-Falls (1,22 € erwartet vs. 1,66 € tatsächlich
— zu hoch, weil der Kontostand seit der Periode weiter gestiegen war).
- [ ] Prüfen/beheben

### F4 — 🟡 Kassenprüfung vergleicht ebenfalls mit dem heutigen statt dem historischen Kontostand
**Datei:** `routes/cash_audits.py:49-50` · **Schweregrad: mittel-hoch**

Gleiches Muster wie F3 (`account_balance()` statt `account_balance_as_of()`).
Wird eine Kassenprüfung nachträglich für ein vergangenes Datum erfasst, wird
die Differenz gegen den heutigen statt den damaligen Saldo berechnet — kann
eine echte Kassendifferenz verschleiern oder eine nicht existierende
vortäuschen.
- [ ] Prüfen/beheben

### F5 — 🟡 Rundungsfunktion für Fehlgeld rundet bei genau 5-Cent-Zwischenwerten teils zu niedrig
**Datei:** `services/money.py:79-84` (`round_to_ten_cents`, genutzt in `events.py:265-303`)
· **Schweregrad: niedrig-mittel**

Nutzt Pythons `round()` (Bankers Rounding): 25 ct → 20 ct, 45 ct → 40 ct,
65 ct → 60 ct (korrekt wäre jeweils aufrunden). Betrifft die Durchschnitts-
Strafe für entschuldigt/unentschuldigt fehlende Mitglieder in seltenen,
exakten Zahlenkonstellationen.
- [ ] Prüfen/beheben

### F6 — 🟡 Strafgeld-Überweisungen ohne Sperre für bereits abgeschlossene Jahre
**Datei:** `routes/penalty_balances.py:32-85` · **Schweregrad: mittel**

Im Gegensatz zu Kassenbuch/Zinsen/Kegelabend-Abrechnung fehlt hier die sonst
übliche `closed_year_block_message()`-Prüfung, obwohl das Buchungsdatum frei
wählbar ist.
- [ ] Prüfen/beheben

### F7 — 🟡 Monatsbeiträge-Verbuchung komplett ohne Sperre für abgeschlossene Jahre
**Datei:** `routes/monthly_contributions.py:694-876` · **Schweregrad: mittel-hoch**

Nur der CSV-Import prüft auf abgeschlossene Jahre, die eigentliche
(Haupt-)Beitragsverbuchung nicht — über `?month=JJJJ-MM` lässt sich ein
abgeschlossenes Jahr rückwirkend erneut bebuchen.
- [ ] Prüfen/beheben

### F8 — ⚪ Jahresabschluss übersieht in „Barzahlung“/„Bahnkosten“ hängengebliebene Kegelabende
**Datei:** `routes/annual_closings.py:93-95, 274` · **Schweregrad: mittel**

Blockiert den Jahresabschluss nur bei Status `"open"`, nicht bei
`"settlement"`/`"lane_cost"` — ein nicht fertig abgerechneter Abend kann so
unbemerkt aus dem Jahresergebnis fallen.
- [ ] Prüfen/beheben

### F9 — ⚪ Monatsbeitrag mit 0,00 €-Soll kann nie automatisch „abgeschlossen“ werden
**Datei:** `routes/monthly_contributions.py:822-827` · **Schweregrad: niedrig-mittel**
- [ ] Prüfen/beheben

### F10 — ⚪ Zins-Dublettenschutz erkennt keine überlappenden Zeiträume
**Datei:** `routes/interest.py:334-347` · **Schweregrad: niedrig-mittel**
- [ ] Prüfen/beheben

### F11 — ⚪ Storno einer automatischen „Barzahlung Strafen“-Buchung synchronisiert das Strafkonto nicht zurück
**Datei:** `routes/cashbook.py:297-357` · **Schweregrad: mittel**
- [ ] Prüfen/beheben

### F12 — ⚪ Startbestände jederzeit ohne Revisions-Protokolleintrag änderbar
**Datei:** `routes/cashbook.py:67-113` · **Schweregrad: mittel**
- [ ] Prüfen/beheben

### Bereits bekannt (aus todo.md, hier nur zur Vollständigkeit referenziert)
- [ ] CSV-Kontoauszug-Re-Upload im selben Monat: Dublettenschutz hängt an
  Dokument-ID, nicht an Zeileninhalt (bewusst zurückgestellt, siehe todo.md)

---

## 2. Sicherheitsprobleme

### S1 — 🔴 KRITISCH: Jede/r „Kassierer/-in“ kann sich (oder jedes Mitglied) per normalem Formular zum Admin machen
**Datei:** `routes/members.py:14-52` (`update_member_login`), Route
`/members/new` + `/members/<id>/edit` (Zeile 71-73, 129-131, erlaubt für
`admin` UND `cashier`) · **Bestätigt durch eigene Codeprüfung.**

`role = request.form.get("role", "member")` wird ungeprüft in `user.role`
übernommen — keine Whitelist. Das Formular (`member_form.html:86-89`) bietet
„Admin“ im Dropdown sogar direkt an. Ein Kassierer-Konto kann darüber ganz
regulär volle Admin-Rechte erlangen. **Das hebelt die komplette
Rollenhierarchie aus — höchste Priorität.**
- [ ] Prüfen/beheben

### S2 — 🔴 KRITISCH: Testdatenbank-Routen ohne jede Anmeldung erreichbar, `DEVELOPER_MODE=true` steht aktuell in der echten `.env`
**Datei:** `app.py:1012-1131` (`/test-database`, `/test-database/switch`,
`/test-database/control`) · **Bestätigt durch eigene Codeprüfung.**

Diese Routen prüfen nur `if not DEVELOPER_MODE: abort(404)` — kein
`@login_required`. Sie sind zudem in `app.py:259` explizit von der
Login-Pflicht ausgenommen (bewusst, damit man vor dem Login umschalten kann).
In eurer echten `.env` ist `DEVELOPER_MODE=true` gesetzt. Wer die
Kegelkasse-Adresse erreicht (z. B. übers Internet, falls Port 8091 dafür
offen ist), könnte ohne Login `/test-database/switch` aufrufen und damit
einen Neustart des Kegelkasse-Prozesses erzwingen bzw. auf die Testdatenbank
umschalten. Die echte Vereinsdatenbank kann laut Code darüber nicht
gelöscht/verändert werden, aber der Betrieb kann gestört werden. **Nicht
live gegen den Produktivcontainer getestet**, nur aus Code + `.env`
abgeleitet — bitte insbesondere die tatsächliche Netzwerk-Erreichbarkeit von
Port 8091 von außen prüfen.
- [ ] Prüfen/beheben

### S3 — 🟠 HOCH: Gespeichertes XSS-Risiko über Dokumenten-Vorschau (hochgeladene HTML-Dateien)
**Datei:** `routes/documents.py:18` (Upload erlaubt `.html`/`.htm`),
`routes/documents.py:260-283` (`document_preview`, `send_file` ohne
`as_attachment`) · **Schweregrad: hoch**

Ein Admin/Kassierer kann eine `.html`-Datei mit `<script>` als „Dokument“
hochladen; öffnet ein anderer Admin die Vorschau, läuft das Skript in dessen
angemeldeter Session.
- [ ] Prüfen/beheben

### S4 — 🟠 HOCH: `/reports` und `/settings/rates` ohne Rollenprüfung — jedes normale Mitglied sieht Finanzdaten aller anderen
**Datei:** `routes/reports.py:508-510`, `routes/finance_settings.py:16-18`
· **Schweregrad: hoch**

Nur `@login_required`, kein `@role_required`, im Gegensatz zu allen
vergleichbaren Finanzseiten. Jedes eingeloggte Mitglied kann individuelle
Zahlungen/Strafen/Anwesenheiten aller anderen Mitglieder einsehen.
- [ ] Prüfen/beheben

### S5 — 🟡 MITTEL: Kein CSRF-Schutz im gesamten Projekt
Kein Flask-WTF/CSRF-Token in Formularen. Durch Browser-Standard `SameSite=Lax`
(implizit, nicht bewusst gesetzt) teilweise entschärft, aber keine echte
Absicherung für sensible Aktionen (Backup wiederherstellen, Konten löschen).
- [ ] Prüfen/beheben

### S6 — 🟡 MITTEL: Kein Brute-Force-Schutz beim Login
**Datei:** `app.py:1134-1151` — unbegrenzte Versuche, kein Lockout, kein Captcha.
- [ ] Prüfen/beheben

### S7 — 🟡 MITTEL: CSV-/Formel-Injection bei Excel-Exporten möglich
**Datei:** `services/export.py:26-58` — Freitext (z. B. aus CSV-Kontoauszugsimport
oder Überweisungstext) mit führendem `=`, `+`, `-`, `@` kann beim Öffnen in
Excel als Formel ausgeführt werden.
- [ ] Prüfen/beheben

### S8 — 🟡 MITTEL: Session-Cookie nicht als `Secure` markiert
`SESSION_COOKIE_SECURE` nirgends gesetzt (Flask-Standard `False`).
- [ ] Prüfen/beheben

### S9 — ⚪ NIEDRIG (Härtung, aktuell nicht akut): Schwache Fallback-Werte in `config.py`
`SECRET_KEY`/`ADMIN_USERNAME`/`ADMIN_PASSWORD` fallen auf unsichere
Standardwerte zurück, falls Umgebungsvariablen fehlen. **Aktuell kein
Risiko**, da eure echte `.env` bereits starke Werte gesetzt hat und
`KEGELKASSE_AUTO_CREATE_ADMIN` deaktiviert ist. Nur als Absicherung für
künftige Neuinstallationen relevant.
- [ ] Prüfen/beheben (niedrige Priorität)

### S10 — ⚪ NIEDRIG: Roh-SQL mit String-Interpolation von Tabellennamen (aktuell nicht ausnutzbar)
`services/schema_migrations.py:23,32`, `routes/backups.py:51,54,122`,
`app.py:118` — keine Nutzereingaben beteiligt, aber unsauberes Muster.
- [ ] Prüfen/beheben (niedrige Priorität)

### Positivbefunde (zur Einordnung, kein Handlungsbedarf)
Keine SQL-Injection (durchgängig ORM), kein `|safe`/`autoescape false` in
Templates, Passwort-Hashing mit `scrypt`, sauberer Passwort-Reset-Flow
(256-Bit-Token, Einmalgebrauch, 24h-Ablauf), Backup-Verschlüsselung
AES-256-GCM + PBKDF2 (600k Iterationen) + RSA-OAEP vorbildlich umgesetzt,
Zip-Slip-Schutz vorhanden, `.env` korrekt per `.gitignore` ausgeschlossen und
nie im Git-Verlauf.

---

## 3. Technische Fehler (allgemeine Robustheit)

### T1 — 🟡 Ändern eines bestehenden Login-Benutzernamens ohne Duplikatsprüfung → 500-Fehler
**Datei:** `routes/members.py:29-33` (anders als beim Neuanlegen, Zeile 42-45)
- [ ] Prüfen/beheben

### T2 — ⚪ Unbehandelte `ValueError` bei `int(request.form.get(...))`
**Dateien:** `routes/admin.py:41,320`, `routes/documents.py:171,194` — führt
zu 500-Fehler statt Fehlermeldung bei nicht-numerischer Eingabe.
- [ ] Prüfen/beheben

### T3 — ⚪ E-Mail-Adressen werden beim Speichern nicht validiert
**Dateien:** `routes/members.py`, `app.py:1254` — `validate_email_format`
wird nur beim Setup/Mail-Einstellungen genutzt, nicht bei Mitglieder-/
Konto-E-Mails. Fehlerhafte Adresse kann später beim Mailversand zu einem
500-Fehler führen (`services/mail.py`, Header-Aufbau außerhalb try/except).
- [ ] Prüfen/beheben

### T4 — ⚪ Ungültiger Rollenwert fällt still auf „admin“ zurück
**Datei:** `routes/admin.py:232-233` (`create_system_user`)
- [ ] Prüfen/beheben

### T5 — ⚪ Nebenwirkung bei reinem Lesezugriff: Teilnehmer-Datensätze werden schon beim ersten GET angelegt
**Datei:** `routes/events.py:1034-1043` — passiert auch, wenn nur ein
Kassenprüfer („auditor“, reine Leserolle) die Seite ansieht.
- [ ] Prüfen/beheben

---

## 4. Mobil vs. Desktop

### M1 — 🔴 Hauptmenüs (Finanzen, Einstellungen, Administration, Auswertungen) öffnen sich nur per Maus-Hover — auf dem Handy vermutlich gar nicht erreichbar
**Datei:** `templates/base.html:70` (`.nav-dropdown:hover .nav-dropdown-content`),
Menüpunkte Zeile 294-357 · **Selbst im Code gegengeprüft — bestätigt.**
· **Schweregrad: hoch**

Die Menü-Auslöser sind reine `<span class="nav-dropbtn">`-Elemente ohne
Link, `href`, `onclick`, `tabindex` oder ARIA-Attribute. Es gibt **keinen**
Klick-/Touch-Handler dafür (anders als beim eigens mit `click`-Handler
versehenen Datenbank-Umschalter in derselben Datei). Auf einem Touchscreen
ohne Maus lässt sich `:hover` auf einem einfachen `<span>` normalerweise
nicht zuverlässig auslösen. Damit wären auf dem Handy potenziell **nicht
erreichbar**: das komplette Finanzen-Menü (Kassenbuch, Monatsabschluss Bank,
Monatsbeiträge, Zinsen, Kassenprüfung, Jahresabschluss, Dokumente,
Revisionsprotokoll), das Einstellungen-Menü und das komplette
Administrationsmenü. Auffällig: Es gibt bereits eine eigene
Mobil-CSS-Regel (Zeile 145), die zeigt, dass ein Aufklappen auf Mobile
vorgesehen war — der Klick-Mechanismus dafür fehlt aber. **Das ist
vermutlich der wichtigste Befund zu deiner Frage „funktioniert Mobile
genauso wie PC“ — nach aktuellem Codestand: nein, große Teile der App
wären auf dem Handy nicht nutzbar.** Bitte einmal am eigenen Handy
gegentesten, ob sich die Menüs wirklich nicht öffnen lassen (manche mobilen
Browser simulieren beim ersten Tippen ein Hover — bei einfachen `<span>`
ohne Link-Charakter ist das aber unüblich).
- [ ] Prüfen/beheben

### M2 — 🟡 Zähl-Buttons (+/−) für Strafen unter Touch-Mindestgröße — werden auf dem Handy sogar noch kleiner
**Datei:** `templates/event_detail.html` (`.counter-control button`):
Desktop ≈39px, ab 760px nur noch ≈35px · **Schweregrad: mittel**

Die am häufigsten genutzte Bedienfläche beim mobilen Erfassen von Strafen.
Zum Vergleich: für Geldbeträge-Felder in der mobilen Kartenansicht wurde
bewusst `min-height: 44px` gesetzt — hier fehlt das.
- [ ] Prüfen/beheben

### M3 — ⚪ Trefferzahl-Feld ohne virtuelle Tastatur auf dem Handy
**Datei:** `templates/event_detail.html:889` (`inputmode="none"`) —
am PC per Tastatur direkt eintippbar, am Handy nur über die kleinen
+/−-Buttons (siehe M2) hochzählbar · **Schweregrad: niedrig-mittel**
- [ ] Prüfen/beheben

### M4 — ⚪ Diagramm-Tooltips nur per Maus-Hover, keine Touch-Unterstützung
**Datei:** `static/js/charts.js:174-177, 235-238` — abgemildert, da dieselben
Werte zusätzlich als Tabelle unter den Diagrammen stehen · **Schweregrad: niedrig**
- [ ] Prüfen/beheben

### M5 — ⚪ Kleine Aktions-Buttons in breiter, scrollbarer Tabelle
**Datei:** `templates/monthly_contributions.html:70-71, 111` — kein
Funktionsverlust, aber unangenehm kleine Tippfläche · **Schweregrad: niedrig**
- [ ] Prüfen/beheben

### Geprüft und unauffällig
`my_event.html`/`my_event_partial.html`, `my_stats.html`,
`my_monthly_contributions.html`, `interest.html` (Desktop/Mobile-Titel sind
nur Textvarianten ohne Datenbezug), Dokumenten-Modal, alle Geldfelder in
unterwegs-relevanten Formularen haben korrektes `inputmode="decimal"`. Das
bereits von dir vorbereitete Fix in `event_detail.html`
(Strafen/Rest-Anzeige mobil vs. Desktop, siehe Backup-Commit) war die
**einzige** Stelle mit echtem Zahlen-Unterschied zwischen Mobile- und
Desktop-Markup — wurde nochmal gegengeprüft, ist jetzt konsistent.

---

## Vorschlag zur Reihenfolge (nur Vorschlag, du entscheidest)

1. **S1** (Rollen-Eskalation) und **S2** (offene Testdatenbank-Routen) —
   Sicherheitslücken mit dem größten Schaden bei geringstem Aufwand zur
   Ausnutzung.
2. **M1** (Hauptnavigation auf Mobile) — falls sich am Handy bestätigt, dass
   die Menüs nicht aufgehen, ist das ein kompletter Funktionsausfall unterwegs.
3. **F1** (Löschbug bei Kassenbuchungen) — betrifft eure echten, bereits
   gebuchten Vereinsgelder.
4. **F2** (Datumsfilter aktiver Kegelabend) — behebt gleich drei deiner
   gemeldeten Dashboard-Beschwerden auf einmal.
5. **F3/F4** (Zinsen & Kassenprüfung mit falschem Bezugssaldo).
6. Rest nach Bedarf/Zeit.

Sag mir einfach, welche Nummern (F/S/T/M) ich angehen soll — ich fange dann
gezielt damit an, ohne an den anderen etwas zu ändern.
