"""CSV-Kontoauszug-Import: Vorschläge (Kategorie/Person) je CSV-Zeile mit
Konfidenz-Stufe, ein "Lerneffekt" für künftige ähnliche Zeilen, sowie ein
rein informativer Dubletten-Hinweis gegen bereits gebuchte Monatsbeiträge.

Verwendet von routes/monthly_contributions.py (Route
monthly_bank_closing_csv_import, Review-Tabelle "Buchungen prüfen &
übernehmen").
"""
import re

from models import db, CsvImportRule, Member, MonthlyContributionBatch, MonthlyContributionPayment

CONFIDENCE_LEARNED = "learned"
CONFIDENCE_GUESSED = "guessed"
CONFIDENCE_NONE = "none"

# Namensteile unter dieser Länge werden für den Vermutet-Abgleich ignoriert -
# zu kurze Vor-/Nachnamen (z. B. "Al", "Ute") führen sonst zu willkürlichen
# Zufallstreffern in beliebigem Fließtext.
MIN_NAME_MATCH_LENGTH = 3

_DIGIT_RUN_RE = re.compile(r"\d+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_learning_key(text, purpose):
    """Normalisiert Buchungstext + Verwendungszweck zu einem stabilen Lernschlüssel.

    Ziffernfolgen (Belegnummern, Mandatsreferenzen, Referenznummern) werden
    entfernt, damit wiederkehrende Lastschriften/Daueraufträge mit leicht
    unterschiedlicher Referenznummer trotzdem denselben Schlüssel ergeben und
    damit als "gelernt" erkannt werden.
    """
    combined = f"{text or ''} {purpose or ''}"
    combined = _DIGIT_RUN_RE.sub(" ", combined)
    combined = combined.upper()
    combined = _WHITESPACE_RE.sub(" ", combined).strip()
    return combined[:255]


def _matching_active_member(haystack_lower):
    """Sucht unter aktiven Mitgliedern den besten Namenstreffer (Teilstring,
    case-insensitive) im Buchungstext/Verwendungszweck. Bei mehreren
    Treffern gewinnt der längste (spezifischste) Namensteil."""
    best_member = None
    best_length = 0
    for member in Member.query.filter_by(active=True).all():
        for name_part in (member.first_name, member.last_name):
            if not name_part or len(name_part) < MIN_NAME_MATCH_LENGTH:
                continue
            if name_part.lower() in haystack_lower and len(name_part) > best_length:
                best_member = member
                best_length = len(name_part)
    return best_member


def suggest_for_row(text, purpose, amount_cents):
    """Liefert einen Vorschlag (Konfidenz/Kategorie/Person) für eine CSV-Zeile.

    Konfidenz "gelernt" (grün): exakter Treffer im normalisierten
    Lernschlüssel gegen eine zuvor gespeicherte CsvImportRule.
    Konfidenz "vermutet" (gelb): kein gelernter Treffer, aber ein Vor- oder
    Nachname eines aktiven Mitglieds kommt im Text vor - Vorschlag
    "Mitgliedsbeitrag" mit diesem Mitglied.
    Sonst "kein Treffer" (grau): keine Vorbefüllung außer einer
    Standardkategorie passend zum Vorzeichen.
    """
    learning_key = normalize_learning_key(text, purpose)

    if learning_key:
        rule = CsvImportRule.query.filter_by(learning_key=learning_key).first()
        if rule:
            return {
                "confidence": CONFIDENCE_LEARNED,
                "category": rule.category,
                "person": rule.person or "",
                "learning_key": learning_key,
            }

    haystack_lower = f"{text or ''} {purpose or ''}".lower()
    member = _matching_active_member(haystack_lower)
    if member:
        return {
            "confidence": CONFIDENCE_GUESSED,
            "category": "Mitgliedsbeitrag",
            "person": member.display_name(),
            "learning_key": learning_key,
        }

    return {
        "confidence": CONFIDENCE_NONE,
        "category": "Sonstige Einnahme" if amount_cents >= 0 else "Sonstige Ausgabe",
        "person": "",
        "learning_key": learning_key,
    }


def learn_from_confirmation(text, purpose, category, person):
    """Legt die (ggf. vom Kassierer korrigierte) Zuordnung als Lernregel an
    oder aktualisiert sie (upsert über den Lernschlüssel, hit_count hoch).

    Committet nicht selbst - wie beim übrigen Kassenbuch-Code ist der
    Aufrufer für den Commit verantwortlich (siehe routes/cashbook.py).
    """
    learning_key = normalize_learning_key(text, purpose)
    if not learning_key or not category:
        return

    rule = CsvImportRule.query.filter_by(learning_key=learning_key).first()
    if rule:
        rule.category = category
        rule.person = person or None
        rule.hit_count = (rule.hit_count or 0) + 1
    else:
        db.session.add(CsvImportRule(
            learning_key=learning_key,
            category=category,
            person=person or None,
            hit_count=1,
        ))


def _period_candidates(row_date):
    """(Jahr, Monat) des Buchungsdatums plus Vor-/Folgemonat - die Wertstellung
    eines Monatsbeitrags läuft nicht immer im selben Kalendermonat wie der
    spätere Buchungstag auf dem Kontoauszug."""
    periods = set()
    for offset in (-1, 0, 1):
        month = row_date.month + offset
        year = row_date.year
        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1
        periods.add((year, month))
    return periods


def monthly_contribution_duplicate_hint(row_date, amount_cents, text, purpose):
    """Weicher Dubletten-Hinweis (nur Anzeige, kein automatischer Ausschluss):
    könnte diese CSV-Zeile zu einem bereits gebuchten Monatsbeitrag passen
    (gleicher Betrag, ähnlicher Zeitraum, Name im Verwendungszweck/
    Buchungstext erkennbar)? Der Kassierer/Admin entscheidet pro Zeile
    selbst, ob trotzdem gebucht wird.
    """
    if not row_date or not amount_cents or amount_cents <= 0:
        return None

    haystack_lower = f"{text or ''} {purpose or ''}".lower()
    periods = _period_candidates(row_date)

    candidates = (
        MonthlyContributionPayment.query
        .join(MonthlyContributionBatch)
        .filter(MonthlyContributionPayment.paid_cents == amount_cents)
        .all()
    )

    for payment in candidates:
        batch = payment.batch
        if not batch or (batch.year, batch.month) not in periods:
            continue
        member = payment.member
        if not member:
            continue
        for name_part in (member.first_name, member.last_name):
            if name_part and len(name_part) >= MIN_NAME_MATCH_LENGTH and name_part.lower() in haystack_lower:
                paid_date_label = payment.paid_date.strftime("%d.%m.%Y") if payment.paid_date else "-"
                return (
                    f"Möglicher Treffer: Monatsbeitrag {batch.period_label()} für "
                    f"{member.display_name()} (Zahlung vom {paid_date_label})"
                )
    return None
