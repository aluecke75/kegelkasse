"""CSV-Kontoauszug-Import: Vorschläge (Kategorie/Person) je CSV-Zeile mit
Konfidenz-Stufe, ein "Lerneffekt" für künftige ähnliche Zeilen, sowie ein
rein informativer Dubletten-Hinweis gegen bereits gebuchte Monatsbeiträge.

Verwendet von routes/monthly_contributions.py (Route
monthly_bank_closing_csv_import, Review-Tabelle "Buchungen prüfen &
übernehmen").
"""
import re
import unicodedata

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

_UMLAUT_TRANSLATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def _norm(text):
    """Vereinheitlicht Text für den Namensvergleich: Kleinschreibung, Umlaute
    als ae/oe/ue, ß als ss, Akzente entfernt. Banken schreiben Namen je nach
    Export unterschiedlich ("Lücke", "Luecke", "LUECKE", "Weßel", "Andrè")."""
    text = (text or "").translate(_UMLAUT_TRANSLATION).casefold()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


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


def _matching_active_members(haystack_lower):
    """Sucht unter aktiven Mitgliedern alle Namenstreffer (Teilstring,
    case-insensitive) im Buchungstext/Verwendungszweck/Zahlungsempfänger.

    Gibt eine Liste von (Mitglied, Länge_des_gefundenen_NamensTeils) zurück,
    sortiert nach Länge absteigend. Bei Namen wie "Ulrich und Heike Dieckmann"
    werden beide Mitglieder erkannt.
    """
    haystack_lower = _norm(haystack_lower)
    scored = []
    seen_ids = set()
    for member in Member.query.filter_by(active=True).all():
        if member.id in seen_ids:
            continue
        score, best_len = _member_name_score(member, haystack_lower)
        if score > 0:
            scored.append((member, score, best_len))
            seen_ids.add(member.id)

    # Nur gleich starke Treffer sind echte Kandidaten: Wer mit Vor- UND
    # Nachnamen im Text steht (z. B. "Timo Wessel"), verdrängt Mitglieder, die
    # nur über einen gleichen Nachnamen ("Melanie Wessel") bzw. einen gleichen
    # Vornamen eines anderen Mitglieds mitgetroffen werden.
    full_first_names = {
        _norm(m.first_name) for m, score, _ in scored if score == _SCORE_FULL
    }
    kept = [
        (m, length) for m, score, length in scored
        if score == _SCORE_FULL
        or (score == _SCORE_FIRST and _norm(m.first_name) not in full_first_names)
    ]
    if not kept:
        # Nur Nachnamen-Treffer (z. B. "Familie Wessel"): alle Träger des Namens.
        kept = [(m, length) for m, score, length in scored if score == _SCORE_LAST]

    kept.sort(key=lambda x: x[1], reverse=True)
    return kept


# Trefferstärke eines Mitglieds im Text: Vor- und Nachname > nur Vorname > nur Nachname.
_SCORE_FULL = 3
_SCORE_FIRST = 2
_SCORE_LAST = 1


def _member_name_score(member, haystack_lower):
    """Liefert (Trefferstärke, Länge des längsten gefundenen Namensteils).

    Vor- und Nachname gefunden (oder Vorname gefunden und kein verwertbarer
    Nachname vorhanden) = _SCORE_FULL; nur der Vorname = _SCORE_FIRST;
    nur der Nachname = _SCORE_LAST; sonst 0. Zu kurze Namensteile zählen nicht.
    """
    def usable(part):
        return bool(part) and len(part) >= MIN_NAME_MATCH_LENGTH

    # haystack_lower muss bereits mit _norm() normalisiert sein.
    first_name, last_name = _norm(member.first_name), _norm(member.last_name)
    first_ok, last_ok = usable(first_name), usable(last_name)
    first_hit = first_ok and first_name in haystack_lower
    last_hit = last_ok and last_name in haystack_lower

    if first_hit and (last_hit or not last_ok):
        score = _SCORE_FULL
    elif first_hit:
        score = _SCORE_FIRST
    elif last_hit:
        score = _SCORE_LAST
    else:
        return 0, 0

    best_len = max(
        len(first_name) if first_hit else 0,
        len(last_name) if last_hit else 0,
    )
    return score, best_len


def _matching_active_member(haystack_lower):
    """Sucht unter aktiven Mitgliedern den besten Namenstreffer (Teilstring,
    case-insensitive) im Buchungstext/Verwendungszweck. Bei mehreren
    Treffern gewinnt der längste (spezifischste) Namensteil."""
    found = _matching_active_members(haystack_lower)
    return found[0][0] if found else None


def _persons_from_learned(person_text):
    """Zerlegt den gespeicherten Personentext einer gelernten Regel (z. B.
    "Melanie Wessel und Timo Wessel") in einzelne Anzeigenamen aktiver
    Mitglieder, in der Reihenfolge ihres Auftretens. Bleibt ein Text ohne
    Mitgliedstreffer (z. B. ein Nicht-Mitglied), wird er unverändert als
    einzige Person geliefert; leerer Text ergibt eine leere Liste."""
    person_text = (person_text or "").strip()
    if not person_text:
        return []

    hits = []
    for member in Member.query.filter_by(active=True).all():
        name = member.display_name()
        pos = person_text.find(name)
        if name and pos >= 0:
            hits.append((pos, name))
    hits.sort()
    if hits:
        return [name for _, name in hits]
    return [person_text]


# Schlüsselwörter, die auf eine Zinsgutschrift hindeuten (case-insensitive,
# als Teilstring geprüft). Wird vor der Mitglieds-Erkennung geprüft, damit
# Zinsen nicht fälschlich als Mitgliedsbeitrag kategorisiert werden.
_INTEREST_KEYWORDS = ("zins", "guthabenzins", "verzinsung", "zinsgutschrift")


# Kontoabschluss-Buchung der Bank (Rechnungsabschluss): enthält Zinsen und ggf.
# Entgelte/Steuern, steht z. B. als "ABSCHLUSS" (CSV) bzw. "Abrechnung
# 30.10.2026 / Wert: 01.11.2026 siehe Anlage Nr. 1" (Sparkasse-PDF) im Auszug -
# das Wort "Zins" kommt in der Buchungszeile selbst nicht vor.
_BANK_CLOSING_RE = re.compile(
    r"(rechnungsabschluss|kontoabschluss|\babschluss\b|\babrechnung\s+\d{1,2}\.\d{1,2}\.\d{2,4}|siehe\s+anlage)",
    re.IGNORECASE,
)


def _is_bank_closing_booking(text, purpose, recipient):
    """True bei einer Kontoabschluss-Buchung der Bank (siehe _BANK_CLOSING_RE)."""
    haystack = f"{text or ''} {purpose or ''} {recipient or ''}"
    return bool(_BANK_CLOSING_RE.search(haystack))


def _is_interest_booking(text, purpose, recipient):
    """Prüft, ob die CSV-Zeile eine Zinsgutschrift ist.

    Gibt True zurück, wenn im Buchungstext, Verwendungszweck oder
    Zahlungsempfänger ein Zins-Schlüsselwort vorkommt.
    """
    haystack = f"{text or ''} {purpose or ''} {recipient or ''}".lower()
    return any(kw in haystack for kw in _INTEREST_KEYWORDS)


def suggest_for_row(text, purpose, amount_cents, recipient=""):
    """Liefert einen Vorschlag (Konfidenz/Kategorie/Person) für eine CSV-Zeile.

    Konfidenz "gelernt" (grün): exakter Treffer im normalisierten
    Lernschlüssel gegen eine zuvor gespeicherte CsvImportRule.
    Konfidenz "vermutet" (gelb): kein gelernter Treffer, aber ein Vor- oder
    Nachname eines aktiven Mitglieds kommt im Buchungstext, Verwendungszweck
    oder Zahlungsempfänger vor - Vorschlag "Mitgliedsbeitrag" mit diesem Mitglied.
    Bei Erkennung von Zinsen (Schlüsselwort "zins") wird die Kategorie
    "Zinsen" vorgeschlagen, ohne ein Mitglied zuzuordnen.
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
                "suggested_persons": _persons_from_learned(rule.person),
                "learning_key": learning_key,
            }

    # Zins-Erkennung vor der Mitglieds-Erkennung: Zinsen sollen nicht als
    # Mitgliedsbeitrag falsch kategorisiert werden.
    # Eine Gutschrift der Bank zum Kontoabschluss ("Abrechnung … siehe Anlage",
    # "ABSCHLUSS") sind die Habenzinsen (netto nach Steuern). Nur bei Gutschriften:
    # eine Belastung zum Abschluss wären Entgelte/Sollzinsen, das nicht raten.
    if _is_interest_booking(text, purpose, recipient) or (
        amount_cents > 0 and _is_bank_closing_booking(text, purpose, recipient)
    ):
        return {
            "confidence": CONFIDENCE_GUESSED,
            "category": "Zinsen",
            "person": "",
            "suggested_persons": [],
            "learning_key": learning_key,
        }

    haystack_lower = f"{text or ''} {purpose or ''} {recipient or ''}".lower()
    members = _matching_active_members(haystack_lower)
    if members:
        # Bei mehreren Mitgliedern (z. B. "Ulrich und Heike Dieckmann") alle
        # in das Person-Feld eintragen, getrennt durch " und "
        person_names = " und ".join(m.display_name() for m, _ in members)
        multi_hint = ""
        if len(members) > 1:
            multi_hint = f" (Achtung: {len(members)} Mitglieder erkannt – bitte prüfen, ob Betrag aufgeteilt werden muss)"
        return {
            "confidence": CONFIDENCE_GUESSED,
            "category": "Mitgliedsbeitrag",
            "person": person_names,
            "suggested_persons": [m.display_name() for m, _ in members],
            "learning_key": learning_key,
            "multi_member_hint": multi_hint,
        }

    return {
        "confidence": CONFIDENCE_NONE,
        "category": "Sonstige Einnahme" if amount_cents >= 0 else "Sonstige Ausgabe",
        "person": "",
        "suggested_persons": [],
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


def monthly_contribution_duplicate_hint(row_date, amount_cents, text, purpose, recipient=""):
    """Weicher Dubletten-Hinweis (nur Anzeige, kein automatischer Ausschluss):
    könnte diese CSV-Zeile zu einem bereits gebuchten Monatsbeitrag passen
    (gleicher Betrag, ähnlicher Zeitraum, Name im Verwendungszweck/
    Buchungstext oder Zahlungsempfänger erkennbar)? Der Kassierer/Admin
    entscheidet pro Zeile selbst, ob trotzdem gebucht wird.
    """
    if not row_date or not amount_cents or amount_cents <= 0:
        return None

    haystack_lower = _norm(f"{text or ''} {purpose or ''} {recipient or ''}")
    periods = _period_candidates(row_date)
    # Dieselbe Erkennung wie beim Personen-Vorschlag: "Timo Wessel" trifft nicht
    # mehr zusätzlich Melanie Wessel über den gemeinsamen Nachnamen.
    matched_member_ids = {m.id for m, _ in _matching_active_members(haystack_lower)}

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
        # Aktive Mitglieder: Treffer wie beim Vorschlag. Ehemalige Mitglieder
        # tauchen dort nicht auf und werden weiter über ihren Namen erkannt.
        if member.active:
            is_match = member.id in matched_member_ids
        else:
            is_match = _member_name_score(member, haystack_lower)[0] > 0
        if is_match:
            paid_date_label = payment.paid_date.strftime("%d.%m.%Y") if payment.paid_date else "-"
            return (
                f"Möglicher Treffer: Monatsbeitrag {batch.period_label()} für "
                f"{member.display_name()} (Zahlung vom {paid_date_label})"
            )
    return None
