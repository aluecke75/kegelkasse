"""Validierungs-Helfer für Formulareingaben (E-Mail, IBAN, BIC, PayPal-Link, Benutzername)."""
import re
import urllib.parse


def clean_username(username):
    return (username or "").strip()


def validate_email_format(value):
    value = (value or "").strip()
    if not value:
        return True
    return re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", value) is not None


def iban_is_valid(iban):
    """Prüft IBAN-Format und Prüfziffer. Leer ist erlaubt."""
    iban = re.sub(r"\s+", "", (iban or "")).upper()
    if not iban:
        return True
    if not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = ""
    for ch in rearranged:
        if ch.isdigit():
            numeric += ch
        elif "A" <= ch <= "Z":
            numeric += str(ord(ch) - 55)
        else:
            return False
    remainder = 0
    for ch in numeric:
        remainder = (remainder * 10 + int(ch)) % 97
    return remainder == 1


def bic_is_valid(bic):
    bic = re.sub(r"\s+", "", (bic or "")).upper()
    if not bic:
        return True
    return re.match(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$", bic) is not None


def paypal_link_is_valid(value):
    value = (value or "").strip()
    if not value:
        return True
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    return parsed.scheme in ("http", "https") and (host.endswith("paypal.me") or host.endswith("paypal.com"))
