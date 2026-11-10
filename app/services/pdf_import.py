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
    documents_path = info["documents_path"]
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

    try:
        with pdfplumber.open(str(target)) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
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