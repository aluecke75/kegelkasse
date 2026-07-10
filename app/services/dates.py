"""Datums-/Monats-Helfer ohne Abhängigkeit von DB oder Request-Kontext."""
from datetime import datetime, timedelta

_MONTH_LABELS_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def first_weekday_of_month(year, month, weekday):
    day = datetime(year, month, 1).date()
    offset = (weekday - day.weekday()) % 7
    return day + timedelta(days=offset)


def add_month(year, month):
    if month == 12:
        return year + 1, 1
    return year, month + 1


def last_day_of_month(year, month):
    next_year, next_month = add_month(year, month)
    return (datetime(next_year, next_month, 1).date() - timedelta(days=1)).day


def nth_weekday_of_month(year, month, weekday, nth):
    first = first_weekday_of_month(year, month, weekday)
    return first + timedelta(days=7 * (nth - 1))


def last_weekday_of_month(year, month, weekday):
    day = datetime(year, month, last_day_of_month(year, month)).date()
    offset = (day.weekday() - weekday) % 7
    return day - timedelta(days=offset)


def _month_label(year, month):
    return f"{_MONTH_LABELS_DE[month - 1]} {str(year)[2:]}"


def _last_n_months(n):
    """Liste von (Jahr, Monat)-Tupeln für die letzten n Monate, älteste zuerst, inkl. aktuellem Monat."""
    today = datetime.today().date()
    months = []
    year, month = today.year, today.month
    for _ in range(n):
        months.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(months))


def parse_month_param(value):
    if not value:
        today = datetime.today().date()
        previous_month = today.replace(day=1) - timedelta(days=1)
        return previous_month.year, previous_month.month

    try:
        parsed = datetime.strptime(value, "%Y-%m").date()
        return parsed.year, parsed.month
    except ValueError:
        today = datetime.today().date()
        previous_month = today.replace(day=1) - timedelta(days=1)
        return previous_month.year, previous_month.month


def month_label(year, month):
    return f"{month:02d}.{year}"


def first_day_of_month(year, month):
    return datetime(year, month, 1).date()


def shift_weekend_to_monday(value_date):
    """Verschiebt Samstag/Sonntag auf den folgenden Montag.

    Feiertage bleiben bewusst außen vor, weil das Bundesland sonst zusätzlich gepflegt werden müsste.
    """
    while value_date.weekday() >= 5:
        value_date += timedelta(days=1)
    return value_date
