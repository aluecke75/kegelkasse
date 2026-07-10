"""Geldbetrag-Helfer: Umrechnung Euro <-> Cent, Rundungsregeln."""
import re
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING, ROUND_FLOOR

from flask import request, flash


def euro_to_cents(value):
    """Wandelt Euro-Eingaben robust in Cent um.

    Unterstützt deutsche Schreibweise mit Tausenderpunkt und Cent-Komma:
    5 -> 5,00 €, 3,7 -> 3,70 €, 1.234,56 -> 1.234,56 €.
    Zur Sicherheit akzeptieren wir auch alte Punkt-Dezimalwerte wie 3.70.
    """
    if value is None:
        return 0

    raw = str(value).strip().replace("€", "").replace(" ", "")

    if raw == "":
        return 0

    # Komfort für mobile Eingabe: ,5 oder .5 bedeutet 0,50 €.
    if raw.startswith((",", ".")):
        raw = "0" + raw

    if not re.fullmatch(r"\d+(?:[.,]\d+)*", raw):
        raise ValueError("Ungültiger Euro-Betrag")

    if "," in raw:
        # Deutsche Schreibweise: Punkte sind Tausendertrenner, Komma trennt Cent.
        cleaned = raw.replace(".", "").replace(",", ".")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts) == 2 and len(parts[1]) <= 2:
            # Alte/technische Schreibweise: 3.70 = 3,70
            cleaned = raw
        elif len(parts[-1]) == 3 and all(len(part) == 3 for part in parts[1:]):
            # 1.234 oder 1.234.567 = Tausenderpunkte ohne Cent.
            cleaned = raw.replace(".", "")
        else:
            raise ValueError("Ungültiger Euro-Betrag")
    else:
        cleaned = raw

    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        raise ValueError("Ungültiger Euro-Betrag")

    return int(round(float(cleaned) * 100))


def form_euro_to_cents(field_name, label):
    try:
        return euro_to_cents(request.form.get(field_name, "0"))
    except ValueError:
        flash(f"Bitte bei '{label}' nur Zahlen eingeben, z. B. 5 oder 3,70.", "danger")
        raise


def cents_to_euro(value):
    amount = (value or 0) / 100
    formatted = f"{amount:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


_TAX_ROUNDING_MODES = {
    "up": ROUND_CEILING,
    "down": ROUND_FLOOR,
    "commercial": ROUND_HALF_UP,
}


def round_tax_cents(value_cents, rounding_mode):
    """Rundet einen Steuer-Cent-Betrag gemäß der gewählten Rundungsregel."""
    quantize_mode = _TAX_ROUNDING_MODES.get(rounding_mode, ROUND_HALF_UP)
    return int(Decimal(value_cents).quantize(Decimal("1"), rounding=quantize_mode))


def round_to_ten_cents(cents):
    """Mathematisch auf den nächsten 0,10-Euro-Schritt runden."""
    cents = cents or 0
    if cents <= 0:
        return 0
    return int(round(cents / 10) * 10)
