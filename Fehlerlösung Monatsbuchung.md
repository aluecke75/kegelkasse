# Fehlerlösung: Monatsbuchung / CSV-Upload in der Kegelkasse

## 1. Fehlersymptom

Beim Hochladen der CSV-Datei in der Funktion **"Buchungen aus Kontoauszug prüfen & übernehmen"** (Monatsabschluss-Assistent) erscheinen Fehlermeldungen. Die CSV-Datei wird nicht korrekt eingelesen, einzelne Zeilen werden als fehlerhaft markiert oder die Übernahme schlägt fehl.

Die beiden Dateien aus dem Zip-Archiv "Fehler Kegelkasse Monatsabrechnung.zip":
- `20261103-23016758-umsatz.CSV` – Kontoauszug-CSV mit Semikolon-Trennung
- `Fehler Monatsabrechnung.jpg` – Screenshot der Fehlermeldung in der App

## 2. Ursachenanalyse

### Ursache A: Falscher Spaltenname für das Datum (HAUPTURSACHE)

Der CSV-Parser in `app/routes/monthly_contributions.py`, Funktion `parse_bank_statement_csv()` (Zeile 178), sucht nach der Spalte **`"Datum"`**:

```python
date_raw = (row.get("Datum") or "").strip()
```

Die hochgeladene CSV-Datei verwendet jedoch die Spalte **`"Buchungstag"`** (siehe CSV-Header):

```
"Auftragskonto";"Buchungstag";"Valutadatum";"Buchungstext";"Verwendungszweck";...;"Betrag";...
```

**Folge:** `row.get("Datum")` liefert immer `None`/leer. Dadurch ist `date_raw` leer, `parse_bank_statement_row_date("")` liefert `None`, und jede Zeile erhält den Fehler:
> Datum konnte nicht gelesen werden: „“

Das macht alle CSV-Zeilen unübernehmbar.

### Ursache B: Zeichencodierung der CSV (mögliche Mitursache)

Die CSV enthält deutsche Umlaute (z. B. `"f�r eine tolle Kegeltour"` mit Ersatzzeichen). Der Parser versucht zuerst `utf-8-sig`, fällt bei `UnicodeDecodeError` auf `cp1252` mit `errors="replace"` zurück. Wenn die Datei eine andere Codierung hat, können Umlaute zerstört werden.

### Vergleich mit GitHub

Der Quellcode auf GitHub (Branch `main`, Datei `app/routes/monthly_contributions.py`) ist **identisch** mit dem lokalen installierten Code. Es gab **keine Änderung** am CSV-Parser, die den Fehler neu eingeführt hätte. Der Fehler existiert also bereits im veröffentlichten Code – er tritt nur auf, weil die Bank der Kegelkasse einen anderen Spaltennamen ("Buchungstag" statt "Datum") liefert als der Parser erwartete.

## 3. Lösung

### Änderung 1: Parser akzeptiert auch "Buchungstag" als Datumsspalte

**Datei:** `/workspace/kegelkasse/app/routes/monthly_contributions.py`  
**Funktion:** `parse_bank_statement_csv()` (ab Zeile 149)

Der Parser soll eine Liste alternativer Spaltennamen für Datum prüfen, statt nur nach `"Datum"` zu suchen. Zusätzlich sollten auch alternative Namen für Betrag, Buchungstext und Verwendungszweck akzeptiert werden, um zukünftige Bank-Exporte robuster zu verarbeiten.

**Alt (Zeile 177-181):**
```python
    for index, row in enumerate(reader, start=1):
        date_raw = (row.get("Datum") or "").strip()
        text_raw = (row.get("Buchungstext") or "").strip()
        purpose_raw = (row.get("Verwendungszweck") or "").strip()
        amount_raw = (row.get("Betrag") or "0").strip()
```

**Neu:**
```python
    _DATE_COLUMNS = ("Datum", "Buchungstag", "Buchungsdatum", "Valutadatum")
    _TEXT_COLUMNS = ("Buchungstext", "BuchungText", "Text", "Beschreibung", "Verwendungszweck / Notizen")
    _PURPOSE_COLUMNS = ("Verwendungszweck", "VerwendungsZweck", "Zweck", "Buchungstext", "Text")
    _AMOUNT_COLUMNS = ("Betrag", "Umsatz", "Betrag (€)", "Wert")

    def _first_value(row, columns):
        for col in columns:
            val = row.get(col)
            if val is not None and str(val).strip():
                return str(val).strip()
        return ""

    for index, row in enumerate(reader, start=1):
        date_raw = _first_value(row, _DATE_COLUMNS)
        text_raw = _first_value(row, _TEXT_COLUMNS)
        purpose_raw = _first_value(row, _PURPOSE_COLUMNS)
        amount_raw = _first_value(row, _AMOUNT_COLUMNS) or "0"
```

### Änderung 2: Robustere Zeichencodierung

**Datei:** `/workspace/kegelkasse/app/routes/monthly_contributions.py`  
**Funktion:** `parse_bank_statement_csv()` (ab Zeile 165)

Die Dekodierung soll mehrere Codierungen versuchen, bevor auf Ersatzzeichen zurückgegriffen wird.

**Alt (Zeile 165-170):**
```python
    raw = file_path.read_bytes()

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
```

**Neu:**
```python
    raw = file_path.read_bytes()

    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-15"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
```

### Änderung 3: Member-Matching prüft auch den Zahlungsempfänger (Empfänger-Spalte)

**Problem:** Die CSV hat eine Spalte **"Beguenstigter/Zahlungspflichtiger"** mit den echten Mitgliedennamen (z. B. "Melanie Wessel", "Timo Wessel"). Das Member-Matching prüfte aber nur "Buchungstext" und "Verwendungszweck" – dort stehen meist nur "mtl Beitrag" oder "Kegelkasse", keine Namen. Deshalb wurden Zahlungen nicht automatisch den Mitgliedern zugeordnet.

**Datei 1:** `/workspace/kegelkasse/app/routes/monthly_contributions.py`  
**Datei 2:** `/workspace/kegelkasse/app/services/csv_import_matching.py`

**Neu in `monthly_contributions.py`:**
```python
    _RECIPIENT_COLUMNS = (
        "Beguenstigter/Zahlungspflichtiger",
        "Beguenstigter",
        "Zahlungspflichtiger",
        "Auftraggeber",
        "Empfänger",
        "Zahlungsempfänger",
    )
```

Der Parser liest zusätzlich die Empfänger-Spalte aus und übergibt sie als `recipient` an `suggest_for_row()` und `monthly_contribution_duplicate_hint()`.

**Neu in `csv_import_matching.py`:**
- `suggest_for_row(text, purpose, amount_cents, recipient="")` – sucht jetzt auch im Zahlungsempfänger nach Mitgliedennamen
- `monthly_contribution_duplicate_hint(..., recipient="")` – prüft auch den Empfänger bei Dubletten-Hinweisen

## 4. Checkliste zur Umsetzung

- [x] Datei `/workspace/kegelkasse/app/routes/monthly_contributions.py` geöffnet und bearbeitet
- [x] In `parse_bank_statement_csv()` die Spaltennamen-Konstanten (`_DATE_COLUMNS`, `_TEXT_COLUMNS`, `_PURPOSE_COLUMNS`, `_AMOUNT_COLUMNS`) und die `_first_value()`-Hilfsfunktion eingefügt (Änderung 1)
- [x] Die vier `row.get(...)`-Aufrufe durch `_first_value(...)`-Aufrufe ersetzt (Änderung 1)
- [x] Die `try/except`-Dekodierung durch mehrstufige Codierungsprüfung ersetzt (Änderung 2)
- [x] `_RECIPIENT_COLUMNS` hinzugefügt und `recipient` in die CSV-Zeilen aufgenommen (Änderung 3)
- [x] `suggest_for_row()` und `monthly_contribution_duplicate_hint()` in `csv_import_matching.py` um `recipient`-Parameter erweitert (Änderung 3)
- [x] Änderungen gespeichert und Syntax-Check bestanden (`python3 -m py_compile`)
- [x] Beide Dateien in laufenden Container kopiert und Container neu gestartet (`docker cp` + `docker restart kegelkasse`)
- [x] Container läuft wieder gesund (healthy)
- [ ] CSV-Datei `20261103-23016758-umsatz.CSV` erneut hochladen und prüfen, ob alle 10 Zeilen korrekt erkannt werden
- [ ] Prüfen, dass die Mitglieder automatisch zugeordnet werden (Melanie Wessel, Timo Wessel, etc. basierend auf "Beguenstigter/Zahlungspflichtiger")
- [ ] Prüfen, dass die Empfänger-Spalte "Empfänger / Zahlungspflichtiger" in der Tabelle angezeigt wird
- [ ] Prüfen, dass bei zwei Mitgliedern (z. B. "Ulrich und Heike Dieckmann") beide Namen im Person-Feld stehen und der Warn-Hinweis erscheint
- [ ] Prüfen, dass Zins-Einträge (z. B. mit "Zinsen" im Verwendungszweck) die Kategorie "Zinsen" vorgeschlagen bekommen und kein Mitglied zugeordnet ist
- [ ] Prüfen, dass die Datumsangaben (z. B. "30.10.26" → 30.10.2026) korrekt parsed werden
- [ ] Prüfen, dass Umlaute (z. B. "für eine tolle Kegeltour") korrekt angezeigt werden
- [ ] Eine Buchung zur Kontrolle übernehmen und im Kassenbuch verifizieren

## 5. Erwartetes Ergebnis nach der Fix

Nach der Änderung sollten alle 10 CSV-Zeilen erfolgreich eingelesen werden:

| # | Buchungstag | Betrag | Empfänger | Verwendungszweck |
|---|-------------|--------|-----------|------------------|
| 1 | 30.10.26 | 1,70 € | (ABRECHNUNG) | Abrechnung 30.10.2026 siehe Anlage |
| 2 | 15.10.26 | 20,00 € | Melanie Wessel | mtl Beitrag |
| 3 | 09.10.26 | 20,00 € | Timo Wessel | monatl. Beitrag |
| 4 | 05.10.26 | 20,00 € | Anke Doring-Schlebes | (leer) |
| 5 | 05.10.26 | 20,00 € | Annette und Norbert Determann | Kegelkasse |
| 6 | 05.10.26 | 20,00 € | Ulrich und Heike Dieckmann | für eine tolle Kegeltour |
| 7 | 01.10.26 | 20,00 € | Wolfgang Gortz | Kegelclub |
| 8 | 01.10.26 | 20,00 € | Achim und Heike Luecke | Kegelkasse |
| 9 | 01.10.26 | 20,00 € | Astrid Gerdes | Kegelkasse |
| 10 | 01.10.26 | 40,00 € | Andre Dolle | Kegelkasse Carolin und Andre |

Alle Beträge sind positiv (Eingänge). Die Mitglieder sollten automatisch anhand der "Beguenstigter/Zahlungspflichtiger"-Spalte zugeordnet werden (z. B. "Melanie Wessel" → Mitglied Melanie Wessel).

## 6. Hinweise

- Die Änderung ist abwärtskompatibel: CSV-Dateien mit der Spalte "Datum" funktionieren weiterhin.
- Bei zukünftigen Bank-Exporten mit anderen Spaltennamen können diese einfach zu den `_*_COLUMNS`-Tupeln hinzugefügt werden.
- Das Encoding-Fallback stellt sicher, dass auch nicht-UTF-8-CSV-Dateien (Windows-Export) gelesen werden können.
- **Zusätzliche Fixes (nach Erstanalyse):**
  - **Empfänger-Spalte angezeigt**: Die CSV-Spalte "Beguenstigter/Zahlungspflichtiger" wird jetzt als eigene Spalte "Empfänger / Zahlungspflichtiger" in der Review-Tabelle angezeigt, damit der Kassierer den Namen des Zahlers sieht.
  - **Mehrere Mitglieder pro Zahlung erkannt**: Bei Zahlungen von zwei Mitgliedern (z. B. "Ulrich und Heike Dieckmann", "Annette und Norbert Determann") werden beide Namen erkannt und im Person-Feld eingetragen (getrennt durch " und "). Es erscheint ein Warn-Hinweis: "Achtung: 2 Mitglieder erkannt – bitte prüfen, ob Betrag aufgeteilt werden muss".
  - **Zinsen automatisch erkannt**: `suggest_for_row()` prüft jetzt vor der Mitglieds-Erkennung, ob ein Zins-Schlüsselwort ("zins", "guthabenzins", "verzinsung", "zinsgutschrift") im Buchungstext, Verwendungszweck oder Zahlungsempfänger vorkommt. Wenn ja, wird die Kategorie "Zinsen" vorgeschlagen (ohne Personen-Zuordnung), damit Zinsen nicht fälschlich als "Mitgliedsbeitrag" gebucht werden.
  - **Template angepasst**: `monthly_bank_closing_csv_import.html` zeigt jetzt die Empfänger-Spalte und den Multi-Member-Hinweis an.

- Der Docker-Build auf Version 0.99.35 konnte nicht abgeschlossen werden (Build bleibt bei Schritt "COPY . ." hängen). Die gepatchten Dateien wurden stattdessen direkt in den laufenden Container kopiert (`docker cp`). Für einen offiziellen Image-Build muss der Docker-Daemon-Problem behoben werden.

## 7. PDF-Kontoauszug-Import (neue Funktion)

### Problem
Kontoauszüge werden häufig als PDF von der Bank bereitgestellt. Bisher konnte die Kegelkasse nur CSV-Dateien im Monatsabschluss-Assistenten verarbeiten. PDF-Kontoauszüge konnten nur als Dokument hochgeladen, aber nicht automatisch in Buchungen umgewandelt werden.

### Lösung

**Datei:** `/workspace/kegelkasse/app/services/pdf_import.py` (neu)
**Datei:** `/workspace/kegelkasse/app/routes/monthly_contributions.py` (erweitert)
**Datei:** `/workspace/kegelkasse/app/requirements.txt` (erweitert)

**Abhängigkeit:** `pdfplumber` zu `requirements.txt` hinzugefügt (für PDF-Textextraktion und Tabellenerkennung).

Der PDF-Extraktor (`parse_bank_statement_pdf()`) unterstützt zwei Extraktionsverfahren:

1. **Tabellen-Extraktion** (`pdfplumber.extract_tables()`): Erkennt Tabellenzeilen mit Datum, Betrag, Empfänger und Verwendungszweck in beliebiger Spaltenreihenfolge.
2. **Zeilen-basierte Extraktion** (Regulärer Ausdruck): Fallback für textbasierte PDFs ohne erkennbare Tabellenstruktur, zeilenweise im Format `TT.MM.JJJJ Betrag Empfänger Verwendungszweck`.

Unterstützte Datumsformate: `TT.MM.JJJJ`, `TT.MM.JJ`, `JJJJ-MM-TT`
Unterstützte Betragsformate: Deutsches Dezimalkomma (z. B. `1.234,56`)

Die Route `monthly_bank_closing_csv_import()` akzeptiert nun sowohl Dokumente mit Kategorie `"Kontoauszug CSV"` als auch `"Kontoauszug PDF"` und ruft je nach Typ den passenden Parser auf. Die Review-Tabelle und die Übernahme-Logik sind identisch für beide Formate.

### Checkliste PDF-Import

- [ ] `pdfplumber` zu `app/requirements.txt` hinzugefügt
- [ ] Neues Modul `app/services/pdf_import.py` erstellt mit `parse_bank_statement_pdf()`
- [ ] `monthly_contributions.py`: Import von `parse_bank_statement_pdf` hinzugefügt
- [ ] `build_csv_import_review_rows()` prüft `document.category` und ruft den passenden Parser auf
- [ ] Dokumentenabfrage filtert beide Kategorien (`Kontoauszug PDF`, `Kontoauszug CSV`)
- [ ] Übernahme-Logik und Audit-Log generisch (nicht mehr CSV-spezifisch, sondern "Kontoauszug-Import")
- [ ] Template zeigt Empfänger-Spalte für PDF-Extraktionen an
- [ ] PDF-Datei `Konto_0023016758-Auszug_2026_0010.pdf` im Kegelkasse-Root liegt vor
- [ ] Container neu gestartet und gesund (healthy)
- [ ] PDF-Import im Monatsabschluss-Assistenten testen: PDF hochladen → Review anzeigen → Buchungen übernehmen