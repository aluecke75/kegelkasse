# Kegelkasse – Statistik-Prüfung 2026-09-22

Reine **Fund-Liste zum Durchsehen**, wie schon `FEHLERPRUEFUNG_2026-09-14.md`. Es wurde
**nichts am Code verändert**. Auftrag: alle Angaben auf dem Dashboard prüfen (Mitglieder- und
Admin-Sicht), dabei "Kegelkönig und co", die Statistikseite und die PDF-Ausdrucke.

Vorgehen: 3 Agenten (Dashboard, Statistik-/Ranglisten-Seite, PDF/Export) haben den Code
gelesen und gegen die **Demo-Datenbank** nachgerechnet. Die auffälligsten Funde habe ich
zusätzlich selbst **lesend gegen die echte Produktiv-Datenbank** verifiziert (nur Zähl-Abfragen,
keine Inhalte geändert). Wo das der Fall war, steht "produktiv bestätigt" dabei.

Zwei Entscheidungen für die spätere Umsetzung stehen schon fest (mit dir besprochen):
- **Königs-Wertungen künftig automatisch für jede Zählstrafe**, nicht nur Pumpe/Kranz.
- **Bei Gleichstand alle zeigen, Platz geteilt** (1, 1, 1, 1, 5).

---

## A. Selbst verifiziert (auch gegen echte Daten), schwerwiegend

### D1 — 🔴 Kein einziges Diagramm wird gezeichnet
**Dateien:** `templates/base.html:340` (`{% block content %}`), `:468` (`<script src=".../charts.js">`),
`templates/reports.html:477`, `templates/my_stats.html:64`

Das Render-Skript, das die Diagramme zeichnet, steht **innerhalb** von `{% block content %}` — es läuft
also beim Parsen von `<main>`, **bevor** `charts.js` (ganz am Seitenende, ohne `defer`) geladen ist.
`window.KegelCharts` ist zu dem Zeitpunkt `undefined`, der Wächter `if (!window.KegelCharts) return;`
bricht das Skript still ab, ohne Fehlermeldung.

**Selbst im laufenden Demo-Container gemessen:** Render-Skript bei Byte 47630 der Antwort, `charts.js`
erst bei Byte 56490 — die Bibliothek kommt nachweislich zu spät. Betroffen: alle sechs Diagramme
(Straf-/Anwesenheitsverlauf, Jahresvergleich, Kassenbestand-Verlauf, beide "Meine Statistik"-Diagramme).
Die `.chart-card` hat keine Mindesthöhe, der leere Block kollabiert auf 0 px — man sieht nur die
Überschrift und den Link "Werte als Tabelle anzeigen".
- [ ] Prüfen/beheben

### D2 — 🔴 Gleichstand: nur ein König wird gezeigt, Plätze sind reine Zeilennummern
**Dateien:** `app.py:802-804` (`top_pump = pump_rows[0] if pump_rows else None`, dito Kranz/Fehlzeiten),
`app.py:817-818` (`pump_rank`/`absence_rank` = Listenposition), `templates/dashboard.html:180-182,96-108`,
`templates/reports.html:164,180,252,266,280,294,315,388,397` (`{{ loop.index }}` als "Platz")

**Produktiv bestätigt (lesend nachgerechnet):**
- "Alle Neune" 2026: Timo, Norbert, Carolin, Andrè je **5** — Dashboard/Wertung würden nur einen zeigen.
- "Verlorenes Spiel" 2026: Wolfgang und Carolin je **18** — dasselbe Problem.
- Beim Pumpenkönig ist es 2026 noch knapp (Andrè 27, Carolin 26 — kein Gleichstand), aber die Logik
  greift beim nächsten Gleichstand sofort wieder.

Zusätzlich in der Demo-DB: Pumpenkönig 2025 Hans/Peter je 15, Kranzkönig 2024 Hans/Max/Peter je 1,
Kranzkönig 2025 Laura/Peter je 2 — auch dort verschwindet der Zweite/Dritte.
- [ ] Prüfen/beheben (Entscheidung steht: alle zeigen, Platz geteilt)

### D3 — 🔴 Zwei von vier Zählstrafen haben überhaupt keine Wertung
**Datei:** `routes/reports.py:525-526` (Dashboard/Statistik suchen fest nach `penalty_pump`/`penalty_wreath`)

**Produktiv bestätigt:** Bei euch gibt es 4 Zählstrafen — "Pumpe" (395 Zählungen), "Kranz" (2), "Alle
Neune" (**62**) und "Verlorenes Spiel" (**419**, die meistgezählte Strafe im Verein!). Nur Pumpe und
Kranz haben eine Rangliste. "Verlorenes Spiel" und "Alle Neune" erscheinen nirgends, obwohl "Verlorenes
Spiel" öfter erfasst wird als alles andere.

Zusätzlich: Legt ein Admin eine eigene Strafart an, bekommt sie `key=None`
(`routes/finance_settings.py:197`) — sie kann **grundsätzlich nie** eine Wertung bekommen, auch nicht
nach einer Umbenennung.
- [ ] Prüfen/beheben (Entscheidung steht: automatisch für jede Zählstrafe)

### D4 — 🟠 Mitglieder sehen Finanzkarten, deren Links in einen Fehler laufen
**Datei:** `templates/dashboard.html:127` (`{% if current_user.role != "member" %}`) schließt bei `:187`
**vor** den Karten "✅ Kassenprüfung" (`:189-203`) und "📘 Jahresabschluss" (`:205-219`)

Selbst nachgelesen: Mitglieder sehen "Barkasse Differenz", "Bank Differenz", "Gesamtbestand" und "Offene
Strafen" der letzten Kassenprüfung/des letzten Jahresabschlusses — obwohl ihnen die Karte "💰 Kassenstand"
direkt darüber bewusst vorenthalten wird. Die Buttons "Kassenprüfung öffnen"/"Jahresabschluss öffnen"
führen für sie zu Routen mit `@role_required("admin","cashier","auditor")` → **HTTP 403**.
- [ ] Prüfen/beheben

### D5 — 🟡 Export: negative Beträge bekommen ein störendes Apostroph
**Datei:** `services/export.py:26-38`

Das ist eine Regression aus meinem eigenen CSV-Injection-Schutz (v0.99.23, S7): Jeder Text, der mit `-`
beginnt, wird zu `'-...` — das war als Schutz vor `=`/`+`/`-`/`@`-Formeln gedacht, trifft aber jeden
negativen Euro-Betrag. Selbst nachgestellt: `-1,00 €` → `'-1,00 €`. Betrifft die Spalten
"Barkasse/Bank Differenz" im Kassenprüfungs-Export und "Saldo" im Strafkonten-Export.
- [ ] Prüfen/beheben

### D6 — 🟡 Englisches Zahlenformat in zwei Diagramm-Tabellen
**Datei:** `templates/reports.html:352,371` (`{{ (row.income_cents / 100)|round(2) }} €`)

Selbst nachgerechnet: 131592 Cent → `1315.92 €` statt `1.315,92 €`; 20000 Cent → `200.0 €` statt
`200,00 €`; -32540 Cent → `-325.4 €` statt `-325,40 €`. Betrifft "Jahresvergleich Einnahmen/Ausgaben"
und "Kassenbestand-Verlauf". Überall sonst im Projekt wird korrekt `cents_to_euro()` verwendet.
- [ ] Prüfen/beheben

### D7 — 🟡 Zählstrafen bei "entschuldigt/unentschuldigt fehlend" zählen trotzdem für die Wertung
**Datei:** `routes/reports.py:41-55` (kein Filter auf `EventParticipant.status`)

**Produktiv bestätigt:** Ulrich ist am 19.12.2025 als "entschuldigt fehlend" geführt, hat aber trotzdem
1× "Alle Neune" und 2× "Verlorenes Spiel" mit Wert > 0 in der Datenbank stehen. Die Geldberechnung
ignoriert das korrekt (er zahlt nichts dafür, `routes/events.py:241-242`), eine Wertung würde die
Zählung aber mitnehmen. Ursache vermutlich: Eingabefelder werden bei Statuswechsel nur per CSS
ausgeblendet, der Wert bleibt im Formular und wird beim Speichern übernommen.
- [ ] Prüfen/beheben

### D8 — 🟡 Strafen zählen schon vor dem Abschluss, Anwesenheiten erst danach
**Dateien:** `routes/reports.py:116-141,227-235,342` (Strafen: kein Status-Filter) vs. `:53,89,219,277,366`
(Anwesenheit: nur `status == "closed"`)

Strafbuchungen (`event_penalty`) entstehen schon beim Schritt "Barzahlungen verbuchen"
(`routes/events.py:1267-1276`), der den Abend auf Status `lane_cost` setzt — noch nicht `closed`.
Ist ein Abend abgerechnet, aber die Bahnkosten noch nicht eingetragen, taucht er in "Strafgeld gesamt"
schon auf, in "Gespielte Kegelabende" und den Anwesenheitsspalten aber noch nicht.
- [ ] Prüfen/beheben

---

## B. Von den Agenten gemeldet, nicht einzeln von mir nachgeprüft

Diese Funde stammen aus den Agenten-Berichten mit Datei:Zeile-Beleg und (soweit möglich) Nachrechnung
gegen die Demo-Datenbank. Ich habe sie plausibilisiert, aber nicht jeden einzeln gegen die Produktiv-DB
verifiziert.

### K1 — Kassenbuch-Export verliert das Minuszeichen bei Ausgaben
`routes/cashbook.py:42` nutzt `entry.amount_euro()` (immer positiv) statt `signed_amount_euro()`
(existiert bereits, wird im Browser genutzt). CSV/Excel/PDF-Summen wären dadurch falsch, nur die Spalte
"Art" verrät noch Einnahme/Ausgabe.
- [ ] Prüfen/beheben

### K2 — Jahresbericht-PDF zeigt bei noch nicht abgeschlossenen Jahren den heutigen statt den
Stichtags-Kontostand
`routes/annual_closings.py:89-90` nutzt `account_balance()` statt der bereits vorhandenen
`account_balance_as_of(account, date)`. Betrifft nur Jahre ohne gespeicherten Abschluss.
- [ ] Prüfen/beheben

### K3 — PDF-Seitenumbruch rechnet Zeilenumbrüche bei langem Text nicht mit
`services/export.py:107,114,128-139` — bei langen Notizen (z. B. aus CSV-Import) kann der Text den
Seitenfuß überschreiben oder ganz von der Seite verschwinden, ohne Fehlermeldung.
- [ ] Prüfen/beheben

### K4 — "→" wird im PDF zu "?"
`models.py:233`, `routes/cashbook.py:203,210`, `routes/cash_audits.py:85,87` — das Pfeilzeichen ist
nicht in cp1252, `pdf_escape` ersetzt es stillschweigend. Betrifft jede Umbuchung im Kassenbuch-PDF.
- [ ] Prüfen/beheben

### K5 — Kassenbuch-PDF wird unhandlich lang (Kartenlayout statt Tabelle)
Mit den Demo-Daten hochgerechnet: 338 Zeilen → 113 Seiten. Eine Tabelle wäre bei 9 Spalten die
naheliegende Form.
- [ ] Prüfen/beheben

### K6 — Zwei verschiedene Zahlen für "Einnahmen/Ausgaben im Jahr"
Statistikseite (`routes/reports.py:316-321`, Basis `CashbookEntry`) vs. Jahresbericht-PDF/Abschluss
(`routes/annual_closings.py:69-74`, Basis `AccountTransaction`, enthält zusätzlich Eröffnungssalden).
Demo 2024: 1.115,92 € vs. 1.315,92 €, Differenz exakt der Eröffnungssaldo.
- [ ] Prüfen/beheben

### K7 — "Wenigste Strafen" kann den tatsächlich Besten nie zeigen
`routes/reports.py:335-337` filtert Zeilen mit Wert `0` heraus (`if row.get(key, 0)`), bevor sortiert
wird. Ein Mitglied ganz ohne Strafe fehlt in dieser Rangliste komplett; ebenso "niedrigste Strafsumme"
je Abend.
- [ ] Prüfen/beheben

### K8 — Drei verschiedene Tie-Break-Regeln auf einer Seite
Pumpen/Kranz sortieren bei Gleichstand nach `Member.first_name` (`reports.py:62`), Fehlzeiten nach dem
angezeigten Namen mit `casefold()` (`:112`), "Meiste/wenigste Strafen" dreht die Namensrichtung mit dem
`reverse`-Flag der Punktesortierung mit (`:337`).
- [ ] Prüfen/beheben

### K9 — Strafgeld-Überweisung (Strafkonto) erzeugt keinen Kassenbuch-Eintrag
`routes/penalty_balances.py:66-80` legt nur `MemberPenaltyTransaction` + `AccountTransaction` an, keinen
`CashbookEntry` — anders als jeder andere Geldweg im Projekt. Dashboards "Einnahmen dieses Jahr" (aus
`CashbookEntry`) bleibt dadurch unverändert, obwohl Bank/Gesamtkasse steigen.
- [ ] Prüfen/beheben

### K10 — Dashboard rechnet über aktive Mitglieder, Statistik/Jahresabschluss über alle
`app.py:741,753` filtert `active=True`, `routes/reports.py:207` und `routes/annual_closings.py:76`
nicht. Bei einem ausgetretenen Mitglied mit offenem Saldo zeigen Dashboard und Strafkonten-Seite
unterschiedliche Summen für "Offene Strafen".
- [ ] Prüfen/beheben

### K11 — "Meine Statistik" auf dem Dashboard zeigt unbeschriftet nur das laufende Jahr
`app.py:798-800` — Pumpen/Kränze/Fehltage in der Mitglieder-Karte sind nur aktuelles Kalenderjahr, ohne
Jahresangabe (die Admin-Karte "Top-Wertungen" direkt daneben hat sie). Direkt daneben steht "Offenes
Strafkonto" über alle Jahre — zwei Zeiträume in einer Karte ohne Kennzeichnung.
- [ ] Prüfen/beheben

### K12 — Weitere kleinere Funde (gesammelt, niedrige Priorität)
- Gäste tauchen in keiner Rangliste auf (Inner Join auf `Member`) — ggf. gewollt, sollte aber bewusst
  entschieden/beschriftet werden.
- Storno-Gegenbuchungen im Kassenbuch bekommen das heutige Datum statt das Datum der Originalbuchung
  (`routes/cashbook.py:334,341,349`) — verschiebt Jahresergebnisse rückwirkend.
- Hartes Löschen eines Mitglieds ohne Kaskade lässt verwaiste Buchungen zurück (`routes/members.py`,
  kein `PRAGMA foreign_keys=ON` im Projekt).
- "Monatsbeiträge prüfen"-Aufgabe zeigt bei mehreren offenen Monaten den neuesten statt ältesten.
- Aufgelöste Umbuchungen (Bar↔Bank) zählen im Jahresvergleich als Einnahme **und** Ausgabe.
- PNG-Logo-Dekoder ohne Fast-Path: bei sehr großen Logos (~2000×2000 px) geschätzt ~7 Sekunden pro
  Dekodierung, synchron im Upload-Request.
- "Excel"-Export ist technisch HTML mit `.xls`-Endung — aktuelle Excel-Versionen zeigen eine
  Formatwarnung, Beträge landen als Text statt als Zahl.
- [ ] Bei Gelegenheit einzeln durchgehen

---

## C. Geprüft und in Ordnung (kein Fehler)

- Umlaute/€ im PDF sind korrekt kodiert (cp1252 + `/WinAnsiEncoding`), auch in Namen mit Anführungszeichen.
- PDF-Struktur ist byteweise valide (xref-Offsets, Objekt-IDs, `/Size`, `startxref`).
- Export/PDF mit 0 Zeilen erzeugt eine gültige Datei mit "Keine Daten vorhanden.", kein Absturz.
- Cent-zu-Euro-Umrechnung über `cents_to_euro()` ist durchgängig korrekt (außer den Jinja-Stellen unter D6).
- Jahresfilter (`db.extract("year", ...)`) ist technisch korrekt, wo er verwendet wird.
- Ausgefallene Abende (`cancelled`) werden nirgends als Anwesenheit/Abwesenheit mitgezählt; `cancel_event`
  räumt Erfassungswerte sauber auf.
- Kranz-Logik (`target_mode="others"`): Die Zählung liegt beim Werfer, nicht bei den Zahlenden — der
  "Kranzkönig" ist fachlich der Richtige.
- "Meine Statistik" (`/my-stats`) und die Statistikseite (`/reports`) liefern für dasselbe Mitglied und
  Jahr identische Werte (beide nutzen `build_player_overview_rows()`).
- Leere Datenlage überall ohne Division durch Null oder Absturz geprüft.
- Mehrseitige Kegelabend-Protokolle: Kopfzeile wird auf jeder Folgeseite korrekt neu gezeichnet.

---

## Vorschlag zur Reihenfolge (nur Vorschlag, du entscheidest)

1. **D1** (Diagramme) — Blocker, betrifft alle Statistik-Grafiken, vermutlich einfach zu beheben
   (Skript hinter `charts.js` verschieben oder `defer` setzen).
2. **D2 + D3** (Gleichstand + fehlende Wertungen) — genau "Kegelkönig und co", die Entscheidungen stehen
   schon fest.
3. **D7 + D8** (Status-Filter vereinheitlichen) — behebt die inhaltliche Verzerrung der Wertungen.
4. **D4** (Mitglieder-Sichtbarkeit) — sicherheitsnah, schnell zu beheben.
5. **D5 + D6** (Export-Apostroph, Zahlenformat) — je eine Zeile.
6. Rest (Abschnitt B) nach Bedarf, K1/K2/K6/K9 zuerst, da sie echte Geldsummen betreffen.

Sag mir, welche Nummern (D/K) ich angehen soll — wie beim letzten Mal erst auf der Demo, Produktiv erst
nach deiner Bestätigung.
