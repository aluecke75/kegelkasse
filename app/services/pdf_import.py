"""PDF-Kontoauszug-Import: Extrahiert Buchungszeilen aus PDF-Kontoauszügen.

Unterstützt tabellarische Kontoauszüge (pdfplumber) sowie textbasierte
PDFs mit Zeilen im Format "Datum Betrag Empfänger Verwendungszweck".

Gibt Zeilen im gleichen Format zurück wie parse_bank_statement_csv():
{
    "index": int,
    "date": str,          # ISO-Datum (YYYY-MM-DD)
    "text": str,          # Buchungstext
    "purpose": str,       # Verwendungszweck
    "recipient": str,     # Zahlungspflichtiger / Empfänger
    "amount_cents": int,  # Betrag in Cent (positiv = Eingang)
    "amount_euro": str,   # formatierter Betrag (z. B. "20,00")
    "type": str,          # "Eingang" / "Ausgang" / "Fehler"
    "error": str|None,    # Fehlermeldung bei unparsebaren Zeilen
}
"""

import re
from datetime import datetime
from pathlib import Path

import pdfplumber

from services.money import euro_to_cents, cents_to_euro

# Datums-Formate, die in deutschen Kontoauszügen vorkommen
_PDF_DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d")

# Erwartet eine Zeile wie: "30.10.26  1,70  Empfänger  Verwendungszweck"
_LINE_RE = re.compile(
    r"^(?P<date>\d{1,2}\.\d{1,2}\.\d{2,4})\s+"
    r"(?P<amount>-?\d{1,3}(?:\.\d{3})*,\d{2})\s*(?P<currency>[A-Z]{3})?\s+"
    r"(?P<rest>.+)$",
    re.IGNORECASE,
)

# Zeilen, die keine Buchungen sind (Kopfzeile, Summen, etc.)
_SKIP_LINE_RE = re.compile(
    r"^(saldo|summe|gesamt|kontoauszug|seite|datum|betrag|empfänger|zahlungspflichtiger|"
    r"verwendungszweck|buchungstext|auftragskonto|iban|bic|währung|info|kategorie|"
    r"abschluss|guthaben|verfügbar|rahmen|limit)",
    re.IGNORECASE,
)


def _parse_amount(amount_str: str) -> int | None:
    """Wandelt einen deutschen Betragsstring (z. B. '1.234,56') in Cent um."""
    if not amount_str:
        return None
    cleaned = amount_str.replace(".", "").replace(",", ".")
    try:
        return euro_to_cents(cleaned)
    except (ValueError, TypeError):
        return None


def _parse_date(date_str: str) -> str | None:
    """Wandelt ein rohes Datumsstring in ISO-Format (YYYY-MM-DD) um."""
    date_str = (date_str or "").strip()
    if not date_str:
        return None
    for fmt in _PDF_DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _split_recipient_purpose(rest: str) -> tuple[str, str]:
    """Teilt den Rest einer Zeile in Empfänger und Verwendungszweck auf."""
    rest = rest.strip()
    if not rest:
        return "", ""

    parts = re.split(r"\s{2,}|\|", rest, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()

    words = rest.split(None, 1)
    if len(words) == 2:
        return words[0], words[1]
    return rest, ""


def parse_bank_statement_pdf(document, max_rows: int = 5000) -> dict:
    """Extrahiert Buchungszeilen aus einem PDF-Kontoauszug.

    Parameter:
        document: Document-Objekt mit stored_filename und documents_path
        max_rows: Maximale Anzahl zurückzugebender Zeilen

    Returns:
        Dict mit "ok", "message", "rows", "row_count", "income_total_cents",
        "expense_total_cents", "income_total_euro", "expense_total_euro"
    """
    from app import active_database_info

    info = active_database_info()
    documents_path = Path(info["documents_path"])
    target = documents_path / document.stored_filename

    if not target.exists():
        return {
            "ok": False,
            "message": f"PDF-Datei nicht gefunden: {document.stored_filename}",
            "rows": [],
            "row_count": 0,
            "income_total_cents": 0,
            "expense_total_cents": 0,
            "income_total_euro": "0,00",
            "expense_total_euro": "0,00",
        }

    rows = []
    income_total_cents = 0
    expense_total_cents = 0
    interest_breakdown = None

    try:
        with pdfplumber.open(str(target)) as pdf:
            # Versuch 0: Kontoauszug im Sparkassen-Stil ("TT.MM.JJJJText Betrag"
            # mit Detailzeilen darunter). pdfplumber liefert hier oft EINE
            # Tabellenzeile mit mehrzeiligen Zellen - die zeilenweise
            # Tabellen-Auswertung unten findet dann nichts.
            page_texts = [(page.extract_text() or "") for page in pdf.pages]
            rows = _parse_statement_text(page_texts, max_rows)
            interest_breakdown = _parse_interest_breakdown(page_texts)
            for parsed_row in rows:
                if parsed_row["amount_cents"] >= 0:
                    income_total_cents += parsed_row["amount_cents"]
                else:
                    expense_total_cents += abs(parsed_row["amount_cents"])

            # Nur wenn der Text-Parser nichts gefunden hat: bisherige Verfahren.
            pages_to_scan = [] if rows else pdf.pages
            for page_index, page in enumerate(pages_to_scan, start=1):
                if len(rows) >= max_rows:
                    break

                # Versuch 1: Tabellen-Extraktion (funktioniert bei strukturierten Auszügen)
                tables = page.extract_tables()
                for table in tables:
                    if len(rows) >= max_rows:
                        break
                    for table_row in table:
                        if len(rows) >= max_rows:
                            break
                        parsed_row = _parse_table_row(table_row)
                        if parsed_row:
                            rows.append(parsed_row)
                            if parsed_row["amount_cents"] >= 0:
                                income_total_cents += parsed_row["amount_cents"]
                            else:
                                expense_total_cents += abs(parsed_row["amount_cents"])

                # Versuch 2: Zeilen-basierte Extraktion (falls keine Tabellen gefunden)
                if not tables:
                    text = page.extract_text()
                    if not text:
                        continue
                    for line in text.split("\n"):
                        if len(rows) >= max_rows:
                            break
                        parsed_row = _parse_text_line(line)
                        if parsed_row:
                            rows.append(parsed_row)
                            if parsed_row["amount_cents"] >= 0:
                                income_total_cents += parsed_row["amount_cents"]
                            else:
                                expense_total_cents += abs(parsed_row["amount_cents"])

    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "message": f"PDF konnte nicht gelesen werden: {exc}",
            "rows": [],
            "row_count": 0,
            "income_total_cents": 0,
            "expense_total_cents": 0,
            "income_total_euro": "0,00",
            "expense_total_euro": "0,00",
        }

    # Index vergeben (1-basiert, wie bei CSV)
    for i, row in enumerate(rows, start=1):
        row["index"] = i

    return {
        "ok": True,
        "message": "PDF wurde gelesen.",
        "rows": rows,
        "row_count": len(rows),
        "income_total_cents": income_total_cents,
        "expense_total_cents": expense_total_cents,
        "income_total_euro": cents_to_euro(income_total_cents),
        "expense_total_euro": cents_to_euro(expense_total_cents),
        # Brutto-Zinsen und Steuern aus dem Rechnungsabschluss (oder None).
        "interest_breakdown": interest_breakdown,
    }


# Buchungsbeginn im Sparkassen-Stil: "01.10.2026Überw.-Gutschrift 40,00" (Datum
# direkt am Text, Betrag am Zeilenende, Vorzeichen als Suffix "-"/"+" oder S/H,
# manchmal auch als Präfix "-").
_STATEMENT_BOOKING_RE = re.compile(
    r"^(?P<date>\d{2}\.\d{2}\.\d{4})\s*(?P<text>.*?)\s+"
    r"(?P<neg_prefix>-)?(?P<amount>\d{1,3}(?:\.\d{3})*,\d{2})\s*(?P<sign>[+-]|[SH])?$"
)

# Zeilen, nach denen keine Detailzeilen einer Buchung mehr folgen (Kontostand,
# Seitenfuß, Hinweise).
_STATEMENT_STOP_RE = re.compile(
    r"^(kontostand|der kontostand|anzahl anlagen|übertrag|seite|kontoauszug|"
    r"kreissparkasse|sparkasse|bitte beachten|hinweise|rechnungsabschluss|datum\b)",
    re.IGNORECASE,
)

_STATEMENT_MAX_DETAIL_LINES = 6


def _parse_statement_text(page_texts: list[str], max_rows: int) -> list[dict]:
    """Liest Buchungen aus dem Seitentext eines Kontoauszugs.

    Eine Buchung beginnt mit einer Zeile "TT.MM.JJJJ Buchungstext Betrag".
    Die folgenden Zeilen (Name, Verwendungszweck) gehören bis zur nächsten
    Buchung oder bis zu einer Stopp-Zeile (Kontostand, Seitenfuß) dazu.
    Gibt eine leere Liste zurück, wenn der Text nicht diesem Muster entspricht.
    """
    rows: list[dict] = []

    for page_text in page_texts:
        current = None
        detail_lines: list[str] = []

        def flush():
            nonlocal current, detail_lines
            if current is not None:
                current["purpose"] = " ".join(detail_lines).strip()
                rows.append(current)
            current = None
            detail_lines = []

        for raw_line in page_text.split("\n"):
            if len(rows) >= max_rows:
                break
            line = raw_line.strip()
            if not line:
                continue

            m = _STATEMENT_BOOKING_RE.match(line)
            if m:
                flush()
                date_iso = _parse_date(m.group("date"))
                amount_cents = _parse_amount(m.group("amount"))
                if not date_iso or amount_cents is None:
                    continue
                if m.group("neg_prefix") or m.group("sign") in ("-", "S"):
                    amount_cents = -amount_cents
                current = {
                    "index": 0,  # wird später vergeben
                    "date": date_iso,
                    "text": m.group("text").strip(),
                    "purpose": "",
                    "recipient": "",
                    "amount_cents": amount_cents,
                    "amount_euro": cents_to_euro(amount_cents),
                    "type": "Eingang" if amount_cents >= 0 else "Ausgang",
                    "error": None,
                }
                continue

            if current is None:
                continue
            if _STATEMENT_STOP_RE.match(line) or len(detail_lines) >= _STATEMENT_MAX_DETAIL_LINES:
                flush()
                continue
            detail_lines.append(line)

        flush()

    return rows


_AMOUNT = r"(?P<a>\d{1,3}(?:\.\d{3})*,\d{2})"
# Rechnungsabschluss (Anlage zum Kontoauszug), z. B. Sparkasse:
#   "Zinsen für Guthaben 2,31+", "Kapitalertragsteuer 0,58-",
#   "Solidaritätszuschlag 0,03-", "Kirchensteuer 0,00-"
_INTEREST_GROSS_RE = re.compile(rf"^Zinsen(?:\s+f(?:ü|ue)r\s+Guthaben)?\s+{_AMOUNT}\s*\+", re.IGNORECASE)
_INTEREST_CGT_RE = re.compile(rf"^Kapitalertrag(?:s)?steuer\s+{_AMOUNT}\s*-", re.IGNORECASE)
_INTEREST_SOLI_RE = re.compile(rf"^Solidarit(?:ä|ae)tszuschlag\s+{_AMOUNT}\s*-", re.IGNORECASE)
_INTEREST_CHURCH_RE = re.compile(rf"^Kirchensteuer\s+{_AMOUNT}\s*-", re.IGNORECASE)


def _parse_interest_breakdown(page_texts: list[str]) -> dict | None:
    """Liest Brutto-Zinsen und Steuerabzüge aus dem Rechnungsabschluss.

    Gibt {"gross_cents", "capital_gains_tax_cents", "solidarity_tax_cents",
    "church_tax_cents"} zurück oder None, wenn kein Zinsbetrag gefunden wurde.
    """
    found = {"gross": None, "cgt": 0, "soli": 0, "church": 0}
    for page_text in page_texts:
        for raw_line in page_text.split("\n"):
            line = raw_line.strip()
            for key, regex in (
                ("gross", _INTEREST_GROSS_RE), ("cgt", _INTEREST_CGT_RE),
                ("soli", _INTEREST_SOLI_RE), ("church", _INTEREST_CHURCH_RE),
            ):
                m = regex.match(line)
                if m:
                    cents = _parse_amount(m.group("a"))
                    if cents is not None and (key != "gross" or found["gross"] is None):
                        found[key] = cents
    if not found["gross"]:
        return None
    return {
        "gross_cents": found["gross"],
        "capital_gains_tax_cents": found["cgt"],
        "solidarity_tax_cents": found["soli"],
        "church_tax_cents": found["church"],
    }


def _parse_table_row(table_row: list) -> dict | None:
    """Versucht, eine Tabellenzeile als Buchung zu parsen.

    Erwartet Spalten wie: Datum | Betrag | Empfänger | Verwendungszweck
    (Reihenfolge kann variieren, wir suchen nach Datum + Betrag Spalten).
    """
    if not table_row or len(table_row) < 2:
        return None

    # Alle Zellen als Strings normalisieren
    cells = [(c or "").strip() for c in table_row]

    # Datum-Spalte finden
    date_idx = None
    date_val = None
    for i, cell in enumerate(cells):
        d = _parse_date(cell)
        if d:
            date_idx = i
            date_val = d
            break

    if date_idx is None:
        return None

    # Betrag-Spalte finden (eine andere Zelle, die ein Betrag-Muster matcht)
    amount_idx = None
    amount_cents = None
    for i, cell in enumerate(cells):
        if i == date_idx:
            continue
        cents = _parse_amount(cell)
        if cents is not None:
            amount_idx = i
            amount_cents = cents
            break

    if amount_idx is None:
        return None

    # Restliche Zellen als Empfänger / Verwendungszweck
    rest_cells = [
        c for i, c in enumerate(cells)
        if i not in (date_idx, amount_idx) and c
    ]
    rest = " ".join(rest_cells)
    recipient, purpose = _split_recipient_purpose(rest)

    return {
        "index": 0,  # wird später vergeben
        "date": date_val,
        "text": "",
        "purpose": purpose,
        "recipient": recipient,
        "amount_cents": amount_cents,
        "amount_euro": cents_to_euro(amount_cents),
        "type": "Eingang" if amount_cents >= 0 else "Ausgang",
        "error": None,
    }


def _parse_text_line(line: str) -> dict | None:
    """Versucht, eine einzelne Textzeile als Buchung zu parsen."""
    line = line.strip()
    if not line:
        return None

    # Überschriften / Summenzeilen überspringen
    if _SKIP_LINE_RE.match(line):
        return None

    m = _LINE_RE.match(line)
    if not m:
        return None

    date_iso = _parse_date(m.group("date"))
    if not date_iso:
        return None

    amount_cents = _parse_amount(m.group("amount"))
    if amount_cents is None:
        return None

    rest = m.group("rest") or ""
    recipient, purpose = _split_recipient_purpose(rest)

    return {
        "index": 0,  # wird später vergeben
        "date": date_iso,
        "text": "",
        "purpose": purpose,
        "recipient": recipient,
        "amount_cents": amount_cents,
        "amount_euro": cents_to_euro(amount_cents),
        "type": "Eingang" if amount_cents >= 0 else "Ausgang",
        "error": None,
    }